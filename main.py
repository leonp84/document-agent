import os
import uvicorn

# LangSmith account is on the EU instance; Cloud Run doesn't have this env var set
# and there's no need to expose it as a secret — hardcoding is correct here.
os.environ.setdefault("LANGCHAIN_ENDPOINT", "https://eu.api.smith.langchain.com")

from api.app import create_app

app = create_app()

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8080, log_level="info")
