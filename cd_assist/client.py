from dotenv import load_dotenv
from openai import OpenAI
import os
import sys

from cd_assist.print import COMPUTER, CONFUSED, GAMBA, YAHO

MODEL_NAME = None

def validate_openai_model():    
    global MODEL_NAME

    MODEL_NAME = os.getenv("OPENAI_MODEL")

    if not MODEL_NAME:
        print(f"{COMPUTER}{CONFUSED}OPENAI_MODEL is not set. Add it to your .env file and try again! {YAHO}")
        sys.exit(1)
    else:
        print(f"{COMPUTER}{GAMBA}OPENAI_MODEL is set to '{MODEL_NAME}'. Proceeding...")
        return MODEL_NAME

def validate_openai_api_key():

    api_key = os.getenv("OPENAI_API_KEY")

    if not api_key:
        print(f"{COMPUTER}{CONFUSED}OPENAI_API_KEY is not set. Add it to your .env file and try again! {YAHO}")
        sys.exit(1)
    else:
        print(f"{COMPUTER}{GAMBA}OPENAI_API_KEY is set. Proceeding...")
        return api_key

def init_openai_client():
    load_dotenv()

    validate_openai_model()
    api_key = validate_openai_api_key()

    return OpenAI(api_key=api_key)
