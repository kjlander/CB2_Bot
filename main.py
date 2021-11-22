# Purpose: This is the main script of CB2 Bot, a work-in-progress Twitch chat
#          bot that performs basic notification and chat interaction tasks
#          including greeting chat users, thanking followers, cheerers, and
#          subscribers, and storing and executing custom text commands.
# Author: Kyle Lander
# Date: 2021-11

# TODO: Add sound alert functionality to notify the streamer of when someone
#       in the chat says hi to them.
# TODO: Add functionality for other EventSub topics (currently only follow,
#       subscribe, and cheer are supported)

import json
import os
import requests
import schedule
import socket
import sqlite3
import threading
import logging
import urllib.parse as urlparse

from dotenv import load_dotenv
from helpers import (authorize, get_app_access_token, get_user_data, 
                    nuke_eventsubs, subscribe_to_eventsub,
                    verify_signature)
from http.server import BaseHTTPRequestHandler, HTTPServer
from os.path import join, dirname
from time import time
from urllib.parse import parse_qs


# Load environment variables.
dotenv_path = join(dirname(__file__), 'config.env')
load_dotenv(dotenv_path)

# Get the location of the script for creating paths.
__location__ = os.path.realpath(
    os.path.join(os.getcwd(), os.path.dirname(__file__)))

# Define some constants that are needed to connect to the servers.
BOT_USERNAME        = os.environ['BOT_USERNAME']
CALLBACK            = os.environ['CALLBACK']
CHANNEL             = f'#{os.environ["CHANNEL"]}'
CLIENT_ID           = os.environ['CLIENT_ID']
AUTH                = {'Accept': 'application/vnd.twitchtv.v5+json',
                       'Client-ID': CLIENT_ID}
COOLDOWN            = os.environ['COOLDOWN']
DB                  = os.path.join(__location__, os.environ['DB'])
ENDPOINT            = 'https://api.twitch.tv/helix/eventsub/subscriptions'
HTTP_PORT           = int(os.environ['HTTP_PORT'])
IRC_CONNECTION_DATA = ('irc.chat.twitch.tv', 6667)
OAUTH               = f'oauth:{os.environ["OAUTH"]}'
SECRET              = os.environ['SECRET']
APP_ACCESS_TOKEN    = get_app_access_token(CLIENT_ID, SECRET)

# This list contains all users that will be able to execute certain chat 
# commands that should only be performed by moderators. Names will be 
# checked against this list before executing such commands.
MODS = os.environ['MODS']

# Define a list of phrases to respond to as a greeting.
GREETINGS = ['hi', 'hello', 'heyo', 'yo', 'hey', 'salut', 'suh']
# Define a list of users that have said one of the 'hello' variations already.
seen_users = []
# Defina a dictionary that will store instances of the CooldownHandler class.
# Every command will get its own instance of the class.
cooldown_handlers = {}

# Create a socket object and make a connection to the chat ircserver.
ircserver = socket.socket()
ircserver.connect(IRC_CONNECTION_DATA)

# Tell the ircserver who we are.
ircserver.send(bytes('PASS {}\r\n'.format(OAUTH), 'UTF-8'))
ircserver.send(bytes('NICK {}\r\n'.format(BOT_USERNAME), 'UTF-8'))

# This list will hold all previously seen Twitch-Eventsub-Message-Id values.
seen_message_ids = []


# Define a class that will keep track of when a command was last used and
# determine if it can be used again by non-mod users.
class CooldownHandler:
    '''
    A class to keep track of cooldowns for IRC chat commands.

    ...

    Attributes
    ----------
    command : str
        name of the command
    cooldown : int
        length of cooldown in seconds
    last_used : float
        time the command was last used

    Methods
    -------
    is_useable():
        Checks if more time than the cooldown length has passed since the
        command was last used. Returns a boolean: True if the cooldown has
        passed, False if the command is still on cooldown.

    '''
    def __init__(self, command: str, cooldown: int) -> None:
        '''
        Constructs the attriutes for the CooldownHandler object.

        Parameters
        ----------
        command : str
            name of the command
        cooldown : int
            length of cooldown in seconds
        '''
        self.command = command
        self.cooldown = int(cooldown)
        self.last_used = time()

    def is_useable(self) -> bool:
        if time() > self.cooldown + self.last_used:
            self.last_used = time()
            return True

        return False


# Set up the request handler that will listen for requests from Twitch.
# Modified from: https://gist.github.com/mdonkers/63e115cc0c79b4f6b8b3a6b797e485c7
class RequestHandler(BaseHTTPRequestHandler):
    '''
    A class to handle HTTP requests from Twitch, subclassed from 
    BaseHTTPRequestHandler.

    ...

    Methods
    ----------
    do_GET():
        Handles all GET requests from Twitch. This is currently only used for
        handling the OIDC Authorization Code Flow process of authorizing the
        bot for EventSub topics like 'subscribe' that require elevated
        permission from the streamer.

    do_POST():
        Handles all POST requests from Twitch. This is currently used for
        responding to webhook verifications and receiving EventSub
        notifications.

    '''
    def _set_response(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/html')
        self.end_headers()

    def do_GET(self):
        parsed = urlparse.urlparse(self.path).query
        print(f'PARSED: {parsed}\n')

        # Handle GET requests from Twitch
        try:
            code = parse_qs(parsed)['code'][0]
            state = parse_qs(parsed)['state'][0]
            print(f'STATE: {state}\n')
            print(f'LOCAL STATE: {os.environ["STATE"]}\n')
            print(f'CODE: {code}\n')

            if state == os.environ['STATE']:
                request_dict = {
                    'client_id': CLIENT_ID,
                    'client_secret': SECRET,
                    'code': code,
                    'grant_type': 'authorization_code',
                    'redirect_uri': CALLBACK
                }

                response = requests.post('https://id.twitch.tv/oauth2/token', request_dict)
                print(f'RESPONSE: {response}\n')

                self._set_response()

            # Return 403 if the states don't match.
            else:
                self.send_response(403)
                self.end_headers()

        except:
            pass

        logging.info('GET request,\nPath: %s\nHeaders:\n%s\n', str(self.path), str(self.headers))
        self._set_response()
        self.wfile.write('GET request for {}'.format(self.path).encode('utf-8'))

    def do_POST(self):
        content_length = int(self.headers['Content-Length']) # <--- Gets the size of data
        post_data = self.rfile.read(content_length) # <--- Gets the data itself
        logging.info('POST request,\nPath: %s\nHeaders:\n%s\n\nBody:\n%s\n',
                str(self.path), str(self.headers), post_data.decode('utf-8'))

        # This section will handle POST requests that come from Twitch.
        if self.headers['Twitch-Eventsub-Message-Id']:
            message_id = self.headers['Twitch-Eventsub-Message-Id']
            eventsub_timestamp = self.headers['Twitch-Eventsub-Message-Timestamp']
            eventsub_signature = self.headers['Twitch-Eventsub-Message-Signature']

            # Return a 200 status if the message ID has been seen before.
            if message_id in seen_message_ids:
                self._set_response()
                print(f'Previously seen message ID: {message_id}, returning 200.\n')

            # Verify that the request came from Twitch.
            elif verify_signature(SECRET, message_id, eventsub_timestamp, post_data, eventsub_signature) == True:                      
                seen_message_ids.append(message_id)
                payload = json.loads(post_data)

                # If the message is a webhook verification, return the challenge.
                if self.headers['Twitch-Eventsub-Message-Type'] == 'webhook_callback_verification':
                    eventsub_challenge = payload['challenge']
                    challenge_bytes = eventsub_challenge.encode()
                    self.send_response(200)
                    self.send_header('Content-Length', str(len(challenge_bytes)))
                    self.end_headers()
                    self.wfile.write(challenge_bytes)

                # If the message is a notification, take the appropriate action.
                elif self.headers['Twitch-Eventsub-Message-Type'] == 'notification':
                    subscription_type = self.headers['Twitch-Eventsub-Subscription-Type']
                    user_name = payload['event']['user_name']

                    # If someone followed, thank them in chat.
                    if subscription_type == 'channel.follow':
                        sendmsg(f'Thank you for following {user_name}!')
                        self._set_response()

                    # If someone subscribed, thank them in chat.
                    elif subscription_type == 'channel.subscribe':
                        sub_tier = int(int(payload['event']['tier']) / 1000)
                        sendmsg(f'{user_name} subscribed at tier {sub_tier}! Thank you for the support!')
                        self._set_response()

                    # If someone cheered, thank them in chat.
                    elif subscription_type == 'channel.cheer':
                        bits = payload['event']['bits']
                        if payload['event']['is_anonymous'] == False:
                            sendmsg(f'{user_name} cheered {bits} bits! Thank you for the support!')
                        else:
                            sendmsg(f'Anonymous cheered {bits} bits! Thank you for the support!')
                        self._set_response()

                    # More actions for other notification types could be added here

            # Return 403 if the signature verification failed.
            else:
                self.send_response(403)
                self.end_headers()

        else:
            self._set_response()
            self.wfile.write('POST request for {}'.format(self.path).encode('utf-8'))

# This function will define and run an HTTP server with the handler above.
def run(server_class=HTTPServer, handler_class=RequestHandler, port=HTTP_PORT):
    logging.basicConfig(level=logging.INFO)
    server_address = ('', port)
    httpd = server_class(server_address, handler_class)
    logging.info('Starting httpd...\n')
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    httpd.server_close()
    logging.info('Stopping httpd...\n')

# Assign a thread to the run() function above, this lets the request handler
# run forever in a backgrount thread while the rest of the program continues on.
thread = threading.Thread(target=run)
thread.daemon = True


# Define a function that adds a command to the database if it doesn't already exist.
def add_command(message: str, cursor: sqlite3.Cursor):
    # Split the message into a list.
    splitmsg = message.split(' ')
    # Check if this is to be a mod-only command (the 'mod' flag will be provided after the
    # command name, which will be at index 2).
    if splitmsg[1].lower() == 'mod':
        # Set the mod boolean to True if the mod flag is present.
        mod = 1
        # Get the name of the new command, its index depends on whether the 'mod' flag
        # is present.
        command = splitmsg[2].lower()
        # Assemble the command contents into a string, starting index depends on whether
        # the 'mod' flag is present.
        content = ' '.join(splitmsg[3:])

    else:
        mod = 0
        command = splitmsg[1].lower()
        content = ' '.join(splitmsg[2:])

    # Check if the command already exists.
    cursor.execute('SELECT command FROM commands WHERE command = :command',
                  {'command': command})
    # Insert the new command if it doesn't already exist.
    if cursor.fetchone() == None:
        cursor.execute('INSERT INTO commands (command, content, mod) VALUES (?, ?, ?)',
                        (command, content, mod))
        return True

    return False


# Schedule a job to clear out the seen_users list every day at midnight.
def clear_seen_users():
    seen_users.clear()
    sendmsg('/me Seen users list cleared!')


# Define a function that handles commands stored in the database.
def command_handler(command: str, user: str, cursor: sqlite3.Cursor) -> str:
    # Try/except in case of sqlite3 error on query executon.
    try:
        cursor.execute('SELECT command, content, mod FROM commands WHERE command = :command',
                      {'command': command})
        row = cursor.fetchone()
        # Check if nothing was returned, meaning no command was found.
        if row == None:
            return None

        # Return any command if the user is a mod.
        if user in MODS:
            return row[1]

        # Non-mod commands are usable by anyone, but are subject to cooldowns.
        if row[2] == 0:
            # Check if a handler for the command already exists, and then
            # check to see if the command is on cooldown.
            if command in cooldown_handlers:
                cmd = cooldown_handlers[command]
                if cmd.is_useable():
                    return row[1]

                print(f'Command {command} on cooldown.\n')
                return None

            # Create a CooldownHander for the command,
            # then return the command since this will be its first use.
            cooldown_handlers[f'{command}'] = CooldownHandler(command, COOLDOWN)
            return row[1]

        print(f'command_handler: user {user} does not have permission to use !{command}.\n')
        # Return None because the command does exist, the user just did not
        # have permission to use it.
        return None

    except sqlite3.Error:
        print(f'SQLite3 Error raised, returning None.\n')
        return None


# Define a function that takes in text that is decorated with a leading "!", indicating that is
# a command, and execute the appropriate command if it exists.
def command(message: str, name: str, cursor: sqlite3.Cursor, dbconnection: sqlite3.Connection):
    # Remove the leading !, save this in case it's a new command that needs added to the DB.
    message = message[1:]
    # Split the message on spaces and get just the first part (the command name).
    cmd = message.split(' ')[0]
    print(f'Command {cmd} received, issued by user {name}\n')

    # This handles execution of all commands that are stored in the database.
    # Used the walrus operator for simplicity.
    if dbcmd := command_handler(cmd, name, cursor):
        # Command did not exist or user did not have permission
        # to execute the command.
        if dbcmd == None:
            pass
        # Execute the command if one was returned.
        else:
            sendmsg(dbcmd)

    # This block handles all the hardcoded commands.
    # These commands are mod-only and are necessary for the
    # core functionality of the bot. Commands have been arranged
    # according to estimated frequency of use.

    # Shoutout command for referring viewers to other streamers.
    elif cmd == 'so' and name in MODS:
        shoutout = message.split(' ')[1]
        sendmsg(f'Check out {shoutout} at https://twitch.tv/{shoutout} !')

    # Adds a new command to the database.
    elif cmd == 'addcom' and name in MODS:
        if add_command(message, cursor):
            dbconnection.commit()
        else:
            print(f'Failed to add command {cmd}, it may already exist.\n')

    # Deletes a command stored in the database.
    elif cmd == 'delcom' and name in MODS:
        delete_command(message, cursor)
        dbconnection.commit()

    # Subscribes the bot to the channel's 'follow' EventSub topic.
    elif cmd == 'esfollow' and name in MODS:
        print('Subscribing to EventSub Follow.\n')
        # Accessing the env variable here because the CHANNEL variable has a leading '#'.
        subscribe_to_eventsub(APP_ACCESS_TOKEN, CALLBACK, CLIENT_ID, SECRET,
                            get_user_id(os.environ["CHANNEL"]), 'follow')

    # Subscribes the bot to the channel's 'subscribe' and 'cheer' EventSub topics.
    elif cmd == 'essub' and name in MODS:
        print('Subscribing to EventSub Subscribe.\n')
        subscribe_to_eventsub(APP_ACCESS_TOKEN, CALLBACK, CLIENT_ID, SECRET,
                            get_user_id(os.environ["CHANNEL"]), 'subscribe')
        print('Subscribing to EventSub Cheer.\n')
        subscribe_to_eventsub(APP_ACCESS_TOKEN, CALLBACK, CLIENT_ID, SECRET,
                            get_user_id(os.environ["CHANNEL"]), 'cheer')

    # Unsubscribes the bot from all EventSub topics regardless of channel.
    elif cmd == 'nukeeventsubs' and name in MODS:
        print('Deleting all EventSub subscriptions.\n')
        nuke_eventsubs(APP_ACCESS_TOKEN, CLIENT_ID)

    # Disconnects the bot from Twitch chat, closes the database connection,
    # and then performs the rest of the shut down tasks.
    elif cmd == 'disconnect' and name in MODS:
        dbconnection.close()
        shut_down()

    # Initiates the OIDC Authorization Code Flow process.
    elif cmd == 'auth' and name in MODS:
        os.environ['STATE'] = authorize(CALLBACK, CLIENT_ID)

    else:
        print(f'Command {cmd} is not a registered command, or {name} does '
                'not have permission to use it, ignoring.\n')


# Define a function that deletes a command if it exists.
def delete_command(message: str, cursor: sqlite3.Cursor):
    # Split the message into a list.
    splitmsg = message.split(' ')
    # Get just the command name.
    command = splitmsg[1]
    cursor.execute('DELETE FROM commands WHERE command = :command',
                {'command': command})

    print(f'Command {command} deleted.\n')


# Define a function to get a user ID specifically.
def get_user_id(user: str, auth: dict=AUTH) -> str:
    data = get_user_data(user, auth)
    user_id = ''
    for i in data['users']:
        user_id = str(i['_id'])

    return user_id


# Define a function to join a chat channel.
def joinchan(chan: str=CHANNEL):
    ircserver.send(bytes('JOIN {}\r\n'.format(chan), 'UTF-8'))
    sendmsg('/me has joined the chat.')


# Define a function to post messages in chat.
def sendmsg(msg: str, target: str=CHANNEL):
    ircserver.send(bytes('PRIVMSG {} :{}\r\n'.format(target, msg), 'UTF-8'))


# Define a function that shuts down the bot when called.
def shut_down():
    print('Cancelling EventSubs and shutting down...\n')
    nuke_eventsubs(APP_ACCESS_TOKEN, CLIENT_ID)
    sendmsg('/me goes to sleep ResidentSleeper')
    thread.join()
    exit(0)


# Define the main function.
def main():
    # Start the HTTP request handler.
    thread.start()
    # Connect to the bot's database and create a cursor.
    dbconnection = sqlite3.connect(DB)
    dbcursor = dbconnection.cursor()
    # Join the IRC channel (chat).
    joinchan()
    # Schedule the seen users list-clearing task.
    schedule.every().day.at('00:00').do(clear_seen_users)

    while True:
        schedule.run_pending()
        ircmsg = ircserver.recv(2048).decode('UTF-8')
        ircmsg = ircmsg.strip('nr')
        cmd = ''
        name = ''

        # Check the type of message received.
        if ircmsg.find('PRIVMSG') != -1:
            # strip() removes \n characters
            name = ircmsg.split('!', 1)[0][1:].strip()
            message = ircmsg.split('PRIVMSG', 1)[1].split(':', 1)[1].strip()
            print(f'Message: {message}\n')

            # If message starts with a !, indicating a bot command.
            if message[0] == '!':
                command(message, name, dbcursor, dbconnection)

            # See if the user is saying hi.
            elif any(word in message.lower() for word in GREETINGS):

                # Say hi if the user has not been seen lately.
                if name not in seen_users:
                    sendmsg('Hi {} :)'.format(name))
                    seen_users.append(name)

        # Respond to ircserver pings to maintain connection.
        elif ircmsg.find('PING') != -1:
            ircserver.send(bytes('PONG :tmi.twitch.tv\r\n', 'UTF-8'))
            print('Ping response sent.')


if __name__ == '__main__':
    try:
        # Start the chat bot.
        main()
    except KeyboardInterrupt:
        shut_down()
