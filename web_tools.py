import requests

def post(address: str, payload: dict, session: requests.Session = None) -> requests.Response:
    if session is None:
        session = requests.Session()
    
    try:
        return session.post(address, json = payload)
    
    except Exception as _:
        return None

def get(address:str, session: requests.Session = None, payload: dict = None) -> requests.Response:
    if session is None:
        session = requests.Session()
    
    try:
        return session.get(address, params = payload)
    
    except Exception as _:
        return None