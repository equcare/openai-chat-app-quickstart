from dotenv import load_dotenv
import os
from azure.cosmos import CosmosClient, PartitionKey

# Load .env file
load_dotenv()

# Retrieve configuration from environment variables
COSMOS_ENDPOINT = os.getenv("COSMOS_ENDPOINT")
COSMOS_KEY = os.getenv("COSMOS_KEY")

# Verify the values being retrieved
if not COSMOS_ENDPOINT or not COSMOS_KEY:
    raise ValueError("COSMOS_ENDPOINT or COSMOS_KEY is not set.")

# Initialize Cosmos client
client = CosmosClient(COSMOS_ENDPOINT, COSMOS_KEY)
