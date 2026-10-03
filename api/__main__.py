import uvicorn

from . import config

if __name__ == "__main__":
    uvicorn.run("api.main:app", host=config.HOST, port=config.PORT)
