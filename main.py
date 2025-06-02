from fastapi import FastAPI, UploadFile, File, Form
from fastapi import Depends
from pydantic import BaseModel
from uuid import uuid4
import uvicorn
from vector_store import delete_index
from vector_store import save_file, process_and_index, search_similar_documents
from chat_history import save_chat, get_chat
from rag_pipeline import generate_answer
# exceptions handling imports
from fastapi import FastAPI
from exceptions import (
    unhandled_exception_handler,
    validation_exception_handler,
    pymongo_exception_handler,
    http_exception_handler,
)
import os
from pymongo.errors import PyMongoError
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from config import PDF_STORAGE_PATH
from chat_history import collection
from authentication import verify_token
from typing import Optional


app = FastAPI()
app.add_exception_handler(Exception, unhandled_exception_handler)
app.add_exception_handler(RequestValidationError, validation_exception_handler)
app.add_exception_handler(PyMongoError, pymongo_exception_handler)
app.add_exception_handler(StarletteHTTPException, http_exception_handler)


# Request schema for chat endpoint
class ChatRequest(BaseModel):
    file_id: Optional[str] = None       # Ties the chat session to a specific uploaded document
    session_id: str      # For multi-turn conversations
    message: str         # User query

# Upload endpoint: accepts file + file_id
@app.post("/upload", dependencies=[Depends(verify_token)])
async def upload_file(file: UploadFile = File(...), file_id: str = Form(...)):
    file_bytes = await file.read()
    path = save_file(file_bytes, file.filename, file_id, upload_dir="chroma_storage/uploads")
    process_and_index(path, file_id)
    return {"status": "indexed", "file_id": file_id}


# Chat endpoint: accepts a message and returns a response based on document + chat history
@app.post("/chat")
async def chat(req: ChatRequest):
    # Get chat history (if file_id given)
    chat_history = get_chat(req.file_id, req.session_id) if req.file_id else []

    # Decide RAG or LLM mode
    if req.file_id:
        context_documents = search_similar_documents(req.message, req.file_id)
        
        if context_documents:
            response = generate_answer(req.message, context_documents, chat_history)
        else:
            response = "Sorry, I couldn't find any relevant information based on the provided documents."
    else:
        response = generate_answer(req.message, [], chat_history)

    # Add new messages
    new_pair = [
        {"role": "user", "content": req.message},
        {"role": "assistant", "content": response}
    ]
    chat_history.extend(new_pair)

    if req.file_id:
        save_chat(req.file_id, req.session_id, chat_history)

    return {
    "user": req.message,
    "assistant": response
}

# delete data/endpoint
@app.post("/delete_data",dependencies=[Depends(verify_token)])
async def delete_data(file_id: str = Form(...)):
    # Delete file from disk
    file_path = os.path.join(PDF_STORAGE_PATH, f"{file_id}.pdf")
    if os.path.exists(file_path):
        os.remove(file_path)

    # Delete index in ChromaDB
    delete_index(file_id)

    # Delete chat history from MongoDB
    collection.delete_many({"file_id": file_id})

    return {"status": "success", "message": f"Data for file_id '{file_id}' deleted."}

# get chat history  in history/endpoint
@app.get("/history")
async def get_history(file_id: str, session_id: str):
    history = get_chat(file_id, session_id)
    return {"file_id": file_id, "session_id": session_id, "history": history}

# Local development server runner
if __name__ == "__main__":
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
