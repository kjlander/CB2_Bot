# Purpose: This file contains helper functions for the main CB2 Bot script.
# Author: Kyle Lander
# Date: 2021-11


import hashlib
import hmac
import json
from typing import Any
import requests

from secrets import token_hex


# Define a function that initiates the OIDC Authorization Code Flow process.
# This is required to get the correct permissions to access EventSub topics 
# like subscribe. The function returns the state string it generates so that
# it can be compared to the one Twitch sends back for verification.
def authorize(callback: str, client_id: str) -> str:
    '''
    Sends a request to Twitch, initiating the OIDC authorization code flow 
    process.

    Parameters:
        callback (str): A callback URI registered with Twitch.
        client_id (str): The client ID of the application or extension using
                         this function.

    Returns:
        state (str): A 30 character random hex string for comaprison to the 
                     'state' that Twitch will send back in a GET request.
    '''
    # Generate a random 30 character hex string.
    state = token_hex(15)
    auth_url = f'https://id.twitch.tv/oauth2/authorize'\
        '?response_type=code'\
        f'&client_id={client_id}'\
        f'&redirect_uri={callback}'\
        f'&scope=channel:read:subscriptions%20bits:read'\
        f'&state={state}'

    print(f'AUTHORIZATION URL: {auth_url}\n')
    response = requests.get(auth_url)
    print(f'RESPONSE: {response.headers}\n{response.json}')

    return state


# Define a function to get an app access token used for subscribing to EventSub topics.
def get_app_access_token(client_id: str, secret: str) -> str:
    '''
    Gets an app access token from Twitch.

    Parameters:
        client_id (str): The client ID of the application or extension using
                         this function.
        secret (str): The secret of the application or extension using this
                      function.

    Returns:
        access_token (str): An app access token from Twitch.
    '''
    request_dict = {
        'client_id': client_id,
        'client_secret': secret,
        'grant_type': 'client_credentials'
    }

    response = requests.post("https://id.twitch.tv/oauth2/token", data=request_dict)
    json_data = json.loads(response.text)
    access_token = json_data["access_token"]

    return access_token


# Define a function to get data on a twtich user account and return it as JSON.
def get_user_data(username: str, auth: str) -> Any:
    '''
    Gets a user object from Twitch.

    Parameters:
        username (str): A Twitch username.
        auth (dict): A Twitch OAuth token.

    Returns:
        response (JSON): A Twitch user object in JSON format.
    '''
    request = 'https://api.twitch.tv/kraken/users?login='+username
    response = requests.get(request, None, headers=auth)

    return response.json()


# This function will get a list of all EventSub subscriptions the bot has created and then
# send a request to delete each one. Use only when you want all subs deleted. 
def nuke_eventsubs(access_token: str, client_id: str) -> bool:
    '''
    Unsubscribes an application from all EventSub subscriptions.

    Parameters:
        access_token (str): An app access token for the application or
                            extension using this function.
        client_id (str): The client ID of the application or extension using
                         this function.

    Returns:
        Boolean (True) upon completion.
    '''
    headers = {'Client-ID': client_id, 
            'Authorization': 'Bearer ' + access_token}

    response = requests.get(url='https://api.twitch.tv/helix/eventsub/subscriptions', headers=headers)
    print(f"Subscriptions: {response.json()}\n")
    subs_json = response.json()
    for i in subs_json['data']:
        print(f"Unsubbing ID: {i['id']}")

        requests.delete(url=f'https://api.twitch.tv/helix/eventsub/subscriptions?id={i["id"]}',
                        headers=headers)

    return True


# Define a function that subscribes to an EventSub topic.
def subscribe_to_eventsub(access_token: str, callback: str, client_id: str, secret: str, user_id: str, topic: str) -> None:
    '''
    Subscribes an application to a Twitch EventSub topic by sending a POST
    request to Twitch. The rest of the process is handled by the HTTP server.

    Parameters:
        access_token (str): An app access token for the application or
                            extension using this function.
        callback (str): A callback URI registered with Twitch.
        client_id (str): The client ID of the application or extension using
                         this function.
        secret (str): The secret of the application or extension using this
                      function.
        user_id (str): The Twitch user ID of the streamer/channel that the
                       EventSub will be created for (the channel you want to
                       get alerts for).
        topic (str): The EventSub topic that is being subscribed to.

    Returns:
        Nothing returned.
    '''
    # Body of request
    data = {
        'type': f'channel.{topic}',
        'version': '1',
        'condition': {
            'broadcaster_user_id': user_id
        },
        'transport': {
            'method': 'webhook',
            'callback': callback,
            'secret': secret
        }
    }
    # Package json for request and send to begin a new EventSub.
    json_data = json.dumps(data)
    headers = {'Client-ID': client_id, 
               'Authorization': f'Bearer {access_token}', 
               'Content-Type': 'application/json'
              }
    response = requests.post(url='https://api.twitch.tv/helix/eventsub/subscriptions', 
                            data=json_data, headers=headers)
    print(f'Sub Request Response: {response.text}\n')


# Define a function that verifies the signature of an EventSub POST response from Twitch.
#   All args should be provided to the function as strings, except for the body (bytes)
# 
#   This function will return True if the signature is verified, and
#   will return False if the signatures do not match.
def verify_signature(secret: str, es_id: str, es_timestamp: str, es_body_bytes: bytes, es_sig: str) -> bool:
    '''
    Verifies the signature of an EventSub POST response from Twitch by
    calculating a HMAC-SHA256 signature and comparing it to the signature
    sent by Twitch.

    Parameters:
        secret (str): The application's secret.
        es_id (str): The ID of the message from Twitch.
        es_timestamp (str): The timestamp of the message from Twitch.
        es_body_bytes (bytes): The entire body of the message from Twitch.
        es_sig (str): The signature of the message from Twitch.

    Returns:
        Boolean: True if the signature is verified (signatures match),
                 False if the signatures do not match.
    '''
    # Assemble the message that will be encoded for comparison to the Twitch signature.
    hmac_msg = es_id.encode() + es_timestamp.encode() + es_body_bytes
    # Calculate an HMAC-SHA256 string from the assembled message.
    calculated_signature = hmac.new(key=secret.encode(), msg=hmac_msg, digestmod=hashlib.sha256).hexdigest()
    # If the signatures match, return True.
    return bool(calculated_signature == es_sig.removeprefix('sha256='))
