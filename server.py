from fastapi import FastAPI, status, Request, HTTPException
from fastapi.responses import RedirectResponse, JSONResponse
from pydantic import BaseModel
from pymongo import MongoClient
from fastapi.middleware.cors import CORSMiddleware
import string
import random
from datetime import datetime, timezone
import logging
import os
import uuid
import validators

app = FastAPI()

# logs all messages
logging.basicConfig(level=logging.DEBUG)

# allow us to redirect the user
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8000","http://localhost:8001"], # only allows redirects from base URL
    allow_credentials=True,
    allow_methods=['GET'], # only allows for get method 
    allow_headers=['*']
)

# initializes and connects to the database upon the start 
@app.on_event("startup")
def start_db_client():
    uri = os.getenv("MONGO_URI")
    app.mongodb_client = MongoClient(uri)
    app.database = app.mongodb_client.get_database('take-home')
    try:
        app.mongodb_client.admin.command('ping')
        logging.info("You successfully connected to MongoDB!")
    except Exception as e:
        logging.Error("Was not able to connect to MongoDB instance")
        raise Exception(e)
    worker_id = str(uuid.uuid4())
    app.state.worker_id = worker_id

# shutdowns the db client once the app shuts down
@app.on_event("shutdown")
def shutdown_db_client():
    logging.info("Shutting down DB Client")
    app.mongodb_client.close()


def generate_id(length=7):
    """
    uses letters + digits to generate the short url
    reason for URL length to be default 7 is that it will be able massive number to the point that there's no chance for collisions  
    """
    base62 = string.ascii_letters + string.digits 
    return ''.join(random.choice(base62) for _ in range(length))

class ShortenRequest(BaseModel):
    url: str

@app.post("/url/shorten", status_code=status.HTTP_201_CREATED)
async def url_shorten(request: ShortenRequest, info: Request):
    """
    Given a URL, generate a short version of the URL that can be later resolved to the originally
    specified URL.
    """
    if not validators.url(request.url):
        logging.error(f"Invalid URL - WORKER: {app.state.worker_id}")
        raise HTTPException(status_code=400, detail=f"{request.url} is not a valid url")
    # This is to see which worker completed the task
    id = generate_id()
    filter = {
        "short_url": id,
    }
    # returns None if not found
    curr_url = app.database.get_collection("URL_Collection").find_one(filter=filter)
 
    # in the rare case that there's a collision, curr_url will be a type so we will regenerate and attempt until it is a None
    while curr_url:
        logging.info(f"ID was duplicated: REGENERATING - WORKER: {app.state.worker_id}")
        id = generate_id()
        new_filter = {
            "short_url": id,
        }
        curr_url = app.database.get_collection("URL_Collection").find_one(filter=new_filter)
    # document that will be stored in collection
    document = {
        "original_url": request.url,
        "short_url": id,
        "created_at": datetime.now(timezone.utc), # utc is standard time zone 
        "updated_at": datetime.now(timezone.utc),
    }
    ok =  app.database.get_collection("URL_Collection").insert_one(document)
    logging.info(f"Inserted new document into database - WORKER: {app.state.worker_id}")
    host = info.headers.get("host")
    resp = {
        "short_url": f"{host}/r/{id}"
    }
    return JSONResponse(content=resp, status_code=status.HTTP_201_CREATED)


class ResolveRequest(BaseModel):
    short_url: str


@app.get("/r/{short_url}")
async def url_resolve(short_url: str):
    """
    Return a redirect response for a valid shortened URL string.
    If the short URL is unknown, return an HTTP 404 response.
    """
    # This is to see which worker completed the task
    logging.info(f'Request handled by {app.state.worker_id}')
    filter  = {
        "short_url": short_url,
    }
    curr_url =  app.database.get_collection("URL_Collection").find_one(filter=filter)
    # if URL is not found return 404 to user and let them know that generate URL doesn't work
    if curr_url is None:
        logging.error(f"{short_url} was not found in the database - WORKER: {app.state.worker_id}")
        raise HTTPException(status_code=404, detail=f"{short_url} not found")

    url = curr_url['original_url']
    return RedirectResponse(url=url)


@app.get("/")
async def index():
    logging.info(f'Request handled by {app.state.worker_id}')
    return "Your URL Shortener is running!"
