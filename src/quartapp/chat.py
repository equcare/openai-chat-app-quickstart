import json
import os
import uuid
from datetime import datetime
from azure.identity.aio import (
    AzureDeveloperCliCredential,
    ChainedTokenCredential,
    ManagedIdentityCredential,
    get_bearer_token_provider,
)
from azure.cosmos import CosmosClient
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
    # Setup Cosmos DB client
    cosmos_client = CosmosClient(os.getenv("COSMOS_ENDPOINT"), os.getenv("COSMOS_KEY"))
    database = cosmos_client.create_database_if_not_exists(id="chatdb")
    container = database.create_container_if_not_exists(id="chathistory", partition_key="/user_id")
    bp.container = container  # Store the container globally for later use

    # Azure credentials setup
    user_assigned_managed_identity_credential = ManagedIdentityCredential(client_id=os.getenv("AZURE_CLIENT_ID"))
    azure_dev_cli_credential = AzureDeveloperCliCredential(tenant_id=os.getenv("AZURE_TENANT_ID"), process_timeout=60)
    azure_credential = ChainedTokenCredential(user_assigned_managed_identity_credential, azure_dev_cli_credential)

    current_app.logger.info("Using Azure OpenAI with credential")
    token_provider = get_bearer_token_provider(azure_credential, "https://cognitiveservices.azure.com/.default")

    if not os.getenv("AZURE_OPENAI_ENDPOINT"):
        raise ValueError("AZURE_OPENAI_ENDPOINT is required for Azure OpenAI")
    if not os.getenv("AZURE_OPENAI_CHAT_DEPLOYMENT"):
        raise ValueError("AZURE_OPENAI_CHAT_DEPLOYMENT is required for Azure OpenAI")

    # Setup OpenAI client
    bp.openai_client = AsyncAzureOpenAI(
        api_version=os.getenv("AZURE_OPENAI_API_VERSION") or "2024-02-15-preview",
        azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
        azure_ad_token_provider=token_provider,
    )
    bp.openai_model = os.getenv("AZURE_OPENAI_CHAT_DEPLOYMENT")


# Function to log chat interactions to Cosmos DB
async def log_chat_to_cosmos(user_id, user_input, bot_response):
    await bp.container.upsert_item({
        "id": str(uuid.uuid4()),  # Unique ID for each log entry
        "user_id": user_id,
        "user_input": user_input,
        "bot_response": bot_response,
        "timestamp": str(datetime.utcnow())
    })


@bp.route("/chat", methods=["POST"])
async def handle_chat():
    data = await request.get_json()
    user_input = data.get("user_input")
    
    # Call OpenAI API for chat response
    response = await bp.openai_client.create_chat_completion(
        deployment_id=bp.openai_model,
        messages=[{"role": "user", "content": user_input}]
    )
    bot_response = response.choices[0].message['content']

    # Log chat to Cosmos DB
    await log_chat_to_cosmos(user_id="user123", user_input=user_input, bot_response=bot_response)

    return {"response": bot_response}


# Optional: Function to retrieve chat history
async def get_chat_history(user_id):
    query = f"SELECT * FROM c WHERE c.user_id = '{user_id}' ORDER BY c.timestamp DESC"
    items = list(bp.container.query_items(query=query, enable_cross_partition_query=True))
    return items


@bp.after_app_serving
async def shutdown_openai():
    await bp.openai_client.aclose()


@bp.get("/")
async def index():
    return await render_template("index.html")


@bp.post("/chat/stream")
async def chat_handler():
    data = await request.get_json()
    request_messages = data.get("messages", [])
    new_session = data.get("new_session", False)

    # Define the system message
    system_message = {
        "role": "system",
        "content": (
            "Eres Amigo, un coach de dieta y actividad física impulsado por inteligencia artificial, "
            "diseñado específicamente para la comunidad hispana/latina. Proporcionas planes de dieta personalizados "
            "basados en preferencias culturales, seguimiento de actividades con establecimiento de metas y monitoreo de progreso, "
            "así como consejos diarios interactivos y mensajes motivacionales. Tus respuestas deben ser en español, claras, empáticas "
            "y respetuosas de las tradiciones y costumbres culturales de los usuarios. Asegúrate de incorporar alimentos tradicionales "
            "y prácticas culturales en tus recomendaciones para fomentar una experiencia personalizada y efectiva."
        )
    }

    all_messages = [system_message]

    if new_session:
        # Add welcome message for new sessions
        welcome_message = {
            "role": "assistant",
            "content": (
                "¡Hola! Soy Amigo, tu coach personalizado de dieta y actividad física. "
                "Estoy aquí para ayudarte a alcanzar tus objetivos de salud de manera adaptada a tus preferencias culturales. "
                "Para comenzar, cuéntame un poco sobre tus metas o el tipo de apoyo que necesitas hoy."
            )
        }
        all_messages.append(welcome_message)

    # Add user messages
    all_messages.extend(request_messages)

    chat_coroutine = bp.openai_client.chat.completions.create(
        model=bp.openai_model,
        messages=all_messages,
        stream=True,
    )

    @stream_with_context
    async def response_stream():
        try:
            async for event in chat_coroutine:
                event_dict = event.model_dump()
                if event_dict["choices"]:
                    yield json.dumps(event_dict["choices"][0], ensure_ascii=False) + "\n"
        except Exception as e:
            current_app.logger.error(e)
            yield json.dumps({"error": str(e)}, ensure_ascii=False) + "\n"

    return Response(response_stream())
