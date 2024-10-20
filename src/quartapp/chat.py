import json
import os

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

@bp.before_app_serving
async def configure_openai():

    # Use ManagedIdentityCredential with the client_id for user-assigned managed identities
    user_assigned_managed_identity_credential = ManagedIdentityCredential(client_id=os.getenv("AZURE_CLIENT_ID"))

    # Use AzureDeveloperCliCredential with the current tenant.
    azure_dev_cli_credential = AzureDeveloperCliCredential(tenant_id=os.getenv("AZURE_TENANT_ID"), process_timeout=60)

    # Create a ChainedTokenCredential with ManagedIdentityCredential and AzureDeveloperCliCredential
    #  - ManagedIdentityCredential is used for deployment on Azure Container Apps

    #  - AzureDeveloperCliCredential is used for local development
    # The order of the credentials is important, as the first valid token is used
    # For more information check out:

    # https://learn.microsoft.com/azure/developer/python/sdk/authentication/credential-chains?tabs=ctc#chainedtokencredential-overview
    azure_credential = ChainedTokenCredential(user_assigned_managed_identity_credential, azure_dev_cli_credential)
    current_app.logger.info("Using Azure OpenAI with credential")

    # Get the token provider for Azure OpenAI based on the selected Azure credential
    token_provider = get_bearer_token_provider(azure_credential, "https://cognitiveservices.azure.com/.default")
    if not os.getenv("AZURE_OPENAI_ENDPOINT"):
        raise ValueError("AZURE_OPENAI_ENDPOINT is required for Azure OpenAI")
    if not os.getenv("AZURE_OPENAI_CHAT_DEPLOYMENT"):
        raise ValueError("AZURE_OPENAI_CHAT_DEPLOYMENT is required for Azure OpenAI")

    # Create the Asynchronous Azure OpenAI client
    bp.openai_client = AsyncAzureOpenAI(
        api_version=os.getenv("AZURE_OPENAI_API_VERSION") or "2024-02-15-preview",
        azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
        azure_ad_token_provider=token_provider,
    )
    # Set the model name to the Azure OpenAI model deployment name
    bp.openai_model = os.getenv("AZURE_OPENAI_CHAT_DEPLOYMENT")


@bp.after_app_serving
async def shutdown_openai():
    await bp.openai_client.close()


@bp.get("/")
async def index():
    return await render_template("index.html")


@bp.post("/chat/stream")
async def chat_handler():
    data = await request.get_json()
    request_messages = data.get("messages", [])
    new_session = data.get("new_session", False)  # Asegúrate de que el frontend envíe esto

    # Definir el mensaje del sistema
    system_message = {
        "role": "system",
        "content": (
            "Eres Amigo, un coach motivador y cercano que combina la calidez latina con expertise en salud y bienestar. "
            "Tu personalidad es alegre, empática y motivadora - como ese amigo sabio de la familia que siempre tiene un buen consejo "
            "y una palabra de aliento. Características principales:\n\n"
            
            "PERSONALIDAD:\n"
            "- Usas un lenguaje cercano y coloquial, pero siempre profesional\n"
            "- Incorporas dichos y expresiones populares latinos cuando es apropiado\n"
            "- Tienes sentido del humor y mantienes un tono optimista\n"
            "- Eres comprensivo con los desafíos y celebras los pequeños logros\n\n"
            
            "EXPERTO:\n"
            "- Adaptas recetas tradicionales latinos para hacerlas más saludables sin perder sabor\n"
            "- Entiendes las dinámicas familiares y sociales de la cultura latina\n"
            "- Proporcionas alternativas saludables para ocasiones especiales y festividades\n"
            "- Sugieres ejercicios que se pueden hacer en casa o con recursos limitados\n\n"
            
            "ENFOQUE:\n"
            "- Promueves cambios graduales y sostenibles, no dietas restrictivas\n"
            "- Respetas las tradiciones culinarias mientras introduces mejoras saludables\n"
            "- Consideras el presupuesto y acceso a alimentos del usuario\n"
            "- Motivas desde el amor propio y la salud, no desde la vergüenza\n"
            "- Reconoces la importancia de la familia y la comunidad en el proceso\n\n"
            
            "Tu objetivo es ser más que un simple coach - eres un compañero de viaje que inspira, educa y "
            "acompaña a cada persona en su camino hacia una vida más saludable, siempre respetando su "
            "identidad cultural y circunstancias personales."
        )
    }

    all_messages = [system_message]

    if new_session:
        # Añadir el mensaje de bienvenida
        welcome_message = {
            "role": "assistant",
            "content": (
                "¡Hola! 👋 ¡Qué gusto conocerte! Soy Amigo, tu compañero personal en este viaje hacia una vida más saludable. "
                "Como latino que soy, entiendo que la buena salud y la buena comida van de la mano con la alegría de vivir. "
                "No vengo a quitarte tus platos favoritos ni a imponerte rutinas imposibles - ¡vengo a ayudarte a encontrar "
                "ese balance perfecto entre lo delicioso y lo saludable! 💪✨\n\n"
                
                "Cuéntame, ¿qué te gustaría lograr? Ya sea que quieras:\n"
                "• Sentirte con más energía\n"
                "• Mejorar tu alimentación sin renunciar a los sabores de casa\n"
                "• Encontrar ejercicios que se ajusten a tu rutina\n"
                "• O simplemente dar el primer paso hacia una vida más saludable\n\n"
                
                "Estoy aquí para escucharte y crear un plan que funcione para TI, tu familia y tu estilo de vida. "
                "¡Juntos vamos a hacer que este proceso sea divertido y sostenible! ¿Por dónde te gustaría empezar?"
            )
        }
        all_messages.append(welcome_message)

    # Añadir los mensajes del usuario
    all_messages.extend(request_messages)

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
                if event_dict["choices"]:
                    yield json.dumps(event_dict["choices"][0], ensure_ascii=False) + "\n"
        except Exception as e:
            current_app.logger.error(e)
            yield json.dumps({"error": str(e)}, ensure_ascii=False) + "\n"

    return Response(response_stream())
