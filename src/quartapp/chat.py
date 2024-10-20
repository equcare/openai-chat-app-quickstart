import json
import os
from datetime import datetime
import aiofiles  # Import aiofiles for async file I/O

from azure.identity.aio import (
    AzureDeveloperCliCredential,
    ChainedTokenCredential,
    ManagedIdentityCredential,
    get_bearer_token_provider,
)
from openai import AsyncAzureOpenAI
from quart import (
    Blueprint,
    Response,
    current_app,
    render_template,
    request,
    stream_with_context,
)

bp = Blueprint("chat", __name__, template_folder="templates", static_folder="static")

CHAT_LOG_FILE = "chat_log.json"  # The file where the chat logs will be stored

@bp.before_app_serving
async def configure_openai():
    user_assigned_managed_identity_credential = ManagedIdentityCredential(client_id=os.getenv("AZURE_CLIENT_ID"))
    azure_dev_cli_credential = AzureDeveloperCliCredential(tenant_id=os.getenv("AZURE_TENANT_ID"), process_timeout=60)
    azure_credential = ChainedTokenCredential(user_assigned_managed_identity_credential, azure_dev_cli_credential)
    current_app.logger.info("Using Azure OpenAI with credential")

    token_provider = get_bearer_token_provider(azure_credential, "https://cognitiveservices.azure.com/.default")
    if not os.getenv("AZURE_OPENAI_ENDPOINT"):
        raise ValueError("AZURE_OPENAI_ENDPOINT is required for Azure OpenAI")
    if not os.getenv("AZURE_OPENAI_CHAT_DEPLOYMENT"):
        raise ValueError("AZURE_OPENAI_CHAT_DEPLOYMENT is required for Azure OpenAI")

    bp.openai_client = AsyncAzureOpenAI(
        api_version=os.getenv("AZURE_OPENAI_API_VERSION") or "2024-02-15-preview",
        azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
        azure_ad_token_provider=token_provider,
    )
    bp.openai_model = os.getenv("AZURE_OPENAI_CHAT_DEPLOYMENT")


@bp.after_app_serving
async def shutdown_openai():
    await bp.openai_client.close()


@bp.get("/")
async def index():
    return await render_template("index.html")


async def log_chat_message(message):
    """Log the chat message to a JSON file asynchronously."""
    log_entry = {
        "timestamp": datetime.now().isoformat(),
        "role": message["role"],
        "content": message["content"]
    }

    # Ensure the chat log file exists and append the message asynchronously
    if os.path.exists(CHAT_LOG_FILE):
        async with aiofiles.open(CHAT_LOG_FILE, "r+", encoding="utf-8") as file:
            chat_log = json.loads(await file.read())
            chat_log.append(log_entry)
            await file.seek(0)
            await file.write(json.dumps(chat_log, ensure_ascii=False, indent=2))
    else:
        async with aiofiles.open(CHAT_LOG_FILE, "w", encoding="utf-8") as file:
            await file.write(json.dumps([log_entry], ensure_ascii=False, indent=2))


@bp.post("/chat/stream")
async def chat_handler():
    data = await request.get_json()
    request_messages = data.get("messages", [])
    new_session = data.get("new_session", False)

    system_message = {
        "role": "system",
        "content": (
            "Eres Amigo, un coach de dieta y actividad física impulsado por inteligencia artificial, "
            "especialmente diseñado para la comunidad hispana y latina. Tu misión es proporcionar planes de dieta personalizados "
            "que respeten y celebren las tradiciones culinarias y preferencias culturales de tus usuarios. Además, ofreces "
            "seguimiento de actividades físicas con establecimiento de metas realistas y monitoreo de progreso continuo. "
            "Proporcionas consejos diarios interactivos, mensajes motivacionales y apoyo emocional para fomentar un estilo de vida saludable. "
            "Tus respuestas deben ser en español, claras, empáticas y respetuosas de las diversas tradiciones y costumbres culturales de los usuarios. "
            "Incorpora alimentos tradicionales y prácticas culturales en tus recomendaciones para asegurar una experiencia personalizada, "
            "relevante y efectiva. Además, adapta tus sugerencias a las diferentes regiones hispanas, reconociendo la diversidad dentro de la comunidad."
        )
    }

    all_messages = [system_message]
    await log_chat_message(system_message)  # Log the system message asynchronously

    if new_session:
        welcome_message = {
            "role": "assistant",
            "content": (
                "¡Hola! Soy Amigo, tu coach personalizado de dieta y actividad física. "
                "Estoy aquí para ayudarte a alcanzar tus objetivos de salud de manera adaptada a tus preferencias culturales. "
                "Para comenzar, cuéntame un poco sobre tus metas o el tipo de apoyo que necesitas hoy."
            )
        }
        all_messages.append(welcome_message)
        await log_chat_message(welcome_message)  # Log the welcome message asynchronously

    all_messages.extend(request_messages)

    # Log user messages asynchronously
    for message in request_messages:
        await log_chat_message(message)

    chat_coroutine = bp.openai_client.chat.completions.create(
        model=bp.openai_model,
        messages=all_messages,
        stream=True,
    )

    @stream_with_context
    async def response_stream():
        try:
            async for event in await chat_coroutine:
                event_dict = event.model_dump()
                if event_dict.get("choices"):
                    assistant_message = event_dict["choices"][0].get("delta", {}).get("content", "")
                    if assistant_message:
                        await log_chat_message({"role": "assistant", "content": assistant_message})  # Log the assistant's response asynchronously
                        yield json.dumps({"role": "assistant", "content": assistant_message}, ensure_ascii=False) + "\n"
        except Exception as e:
            current_app.logger.error(e)
            yield json.dumps({"error": str(e)}, ensure_ascii=False) + "\n"
