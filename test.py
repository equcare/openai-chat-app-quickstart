import os
from dotenv import load_dotenv
from azure.cosmos import CosmosClient, PartitionKey
from azure.identity import DefaultAzureCredential
from azure.core.credentials import AzureKeyCredential
import openai
from quart import Quart, request, jsonify

# Load environment variables
load_dotenv()

# Environment variables
COSMOS_ENDPOINT = os.getenv("COSMOS_ENDPOINT")
COSMOS_KEY = os.getenv("COSMOS_KEY")
OPENAI_API_VERSION = os.getenv("AZURE_OPENAI_API_VERSION")
OPENAI_CHAT_DEPLOYMENT = os.getenv("AZURE_OPENAI_CHAT_DEPLOYMENT")
OPENAI_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT")

# Initialize Cosmos DB client
client = CosmosClient(COSMOS_ENDPOINT, credential=AzureKeyCredential(COSMOS_KEY))
database_name = 'chatdb'
container_name = 'chathistory'
database = client.create_database_if_not_exists(id=database_name)
container = database.create_container_if_not_exists(
    id=container_name,
    partition_key=PartitionKey(path="/conversationId")
)

# Initialize Quart app
app = Quart(__name__)

# OpenAI API settings
openai.api_type = "azure"
openai.api_version = OPENAI_API_VERSION
openai.api_key = os.getenv("AZURE_OPENAI_API_KEY")
openai.api_base = OPENAI_ENDPOINT

@app.route("/chat", methods=["POST"])
async def chat():
    data = await request.get_json()
    user_input = data.get("message")
    conversation_id = data.get("conversationId", "default")

    # Get OpenAI response
    response = openai.ChatCompletion.create(
        engine=OPENAI_CHAT_DEPLOYMENT,
        messages=[{"role": "user", "content": user_input}]
    )

    openai_response = response['choices'][0]['message']['content']

    # Store chat history in Cosmos DB
    container.upsert_item({
        "conversationId": conversation_id,
        "user_input": user_input,
        "openai_response": openai_response
    })

    return jsonify({
        "response": openai_response
    })

# Start the Quart app
if __name__ == "__main__":
    app.run(port=50505)
