import requests
import os
import aiohttp
import logging
from dotenv import load_dotenv

load_dotenv()
API = os.getenv("FRAG_API")
NUM = os.getenv("NUM")
MNEMONICS = os.getenv("MNEMONICS")
auth_token = os.getenv("AUTH_TOKEN")

def auth():
    try:
        url = "https://api.fragment-api.com/v1/auth/authenticate/"

        payload = {
            "api_key": API,
            "phone_number": NUM,
            "version": "V4R2",
            "mnemonics": ["dance", "else", "dinner", "list", "shiver", "gap", "bag", "comfort", "useless", "now", "order", "social", "require", "chat", "shine", "item", "crowd", "barely", "deliver", "kit", "comic", "hammer", "shuffle", "skirt"]
        }
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json"
        }

        response = requests.post(url, json=payload, headers=headers)
        #print(response.json().get("token"))
        return response.json().get("token")

    except Exception as e:
        print(e)

#Получаем баланс
async def get_balance():
    url = "https://api.fragment-api.com/v1/misc/wallet/"

    headers = {
        "Accept": "application/json",
        "Authorization": f"JWT {auth_token}"
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, timeout=10) as resp:
                if resp.status != 200:
                    logging.warning(f"Fragment API error {resp.status}: {await resp.text()}")
                    return None
                data = await resp.json()
                return float(data.get("balance", 0.0))
    except Exception as e:
        logging.error(f"Ошибка при получении баланса: {e}")
        return None


def check_order(id):
    url = "https://api.fragment-api.com/v1/order/{id}/"

    headers = {
        "Accept": "application/json",
        "Authorization": f"JWT {auth_token}"
    }

    response = requests.get(url, headers=headers)

    print(response.json())


async def buy_stars(id, quantity):
    url = "https://api.fragment-api.com/v1/order/stars/"

    payload = {
        "username": id,
        "quantity": quantity,
        "show_sender": False
    }
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": f"JWT {auth_token}"
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload, headers=headers, timeout=60) as resp:
            resp.raise_for_status()
            data = await resp.json()
            print(data)
            return data


#auth_token = auth()
#print(auth_token)
#id = "ya_g0ga"
#print(auth_token)
#get_balance()
#buy_stars(id, 50)