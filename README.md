# CB2_Bot - A Twitch Chat Bot
#### Video Demo:  https://youtu.be/4e-e2BP4RVM
#### Description:
CB2_Bot is a Twitch chat bot that performs channel interaction tasks such as thanking users when they follow or
subscribe to the Twitch channel, greeting users that haven't been seen in chat for a while, and executing custom
chat commands that may be created and deleted by moderators from Twitch chat.

# Table of Contents
1. [Dependancies](#dependancies)
2. [Files](#files)
3. [Config](#config)
4. [Database](#database)
5. [Commands](#commands)
6. [Plans for the Future](#plans-for-the-future)

## Dependancies:
This project is written in Python 3.9.6

#### Packages

dotenv
hashlib
hmac
http.server
json
logging
os
requests
schedule
secrets
socket
sqlite3
threading
time
urllib.parse

## Files
#### main.py
Contains the main function which has the core logic of the bot. Also contains the HTTP server that handles
GET and POST requests from Twitch, functions that interact with the database, and other small functions that
support bot operation.
#### helpers.py
Contains functions that interact with the Twitch API for authorization and webhook management tasks.
#### config.env
Contains the environment variable definitions required to configure the bot for a uniqe deployment (including
bot name, channel to join, secrets, tokens, moderators, etc.).
#### CB2.db
A SQLite database that stores custom chat commands.

## Config
In order for the bot to connect to a chat channel, you must have a Twitch account, register an application 
at https://dev.twitch.tv/ and generate a client ID and a secret, and you will also need to go to 
https://twitchapps.com/tmi/ to generate an Oauth token on the account that the bot will sign into 
Twitch under.

#### The Following variables must be filled out in the config.env file:

##### BOT_USERNAME (str)
Username the bot will join chat under.
##### CALLBACK (str)
Callback URI for recieving requests from Twitch (Must be public and able to recieve HTTPS on port 443).

If running the bot locally, a service like **ngrok** can be used to create a public https endpoint which then directs
requests to your localhost. https://ngrok.com/

Example in ngrok client:

    $ ngrok http 8000

Then copy the https address the client generates and set CALLBACK to that, then set HTTP_PORT to 8000.

You will also need to set the OAuth Redirect URL for the bot on your Twitch Dev console to the same address.
##### CHANNEL (str)
The name of the Twitch channel the bot will join.
##### CLIENT_ID (str)
The bot's client ID (from your Twitch Dev console).
##### COOLDOWN (int)
Cooldown time in seconds for commands during which time a command wlll not
be useable by non-moderator users to prevent command spam.
##### DB (str)
The name of the database file that will store chat commands incuding extension, like:

    CB2.db
##### HTTP_PORT (int)
The port the HTTP server will listen on for requests from Twitch.
##### MODS (list of strs)
Python list of users that have moderator privileges.
##### OAUTH (str)
The bot account's Oauth token (from https://twitchapps.com/tmi/) not including the leading "oauth:".
##### SECRET (str)
The bot's secret (from your Twitch Dev console).

## Database
The bot makes use of a SQLite database for storing custom chat commands. The database schema is as follows:

    CREATE TABLE IF NOT EXISTS "commands" (id INTEGER PRIMARY KEY, command VARCHAR(25) NOT NULL, content VARCHAR(500) NOT NULL, mod INTEGER NOT NULL);

## Commands
The bot has a few commands in its current state:

#### !addcom
Allows moderators to add new custom commands to the bot. Commands will be stored in the SQLite database and
therefore persist when the bot is not running.

###### Usage:
    !addcom <"mod" flag (optional)> <command name> <command contents>

    !addcom pogchamp Did you see that?! PogChamp

    !addcom mod kappa Golden Kappa check

Command names are not case sensitive, but all capitilization in the command contents is preserverd. Maximum
command name length is 25 characters, and maximum content length is 500 characters, the same as the maximum 
message length in Twitch chat.

If the **"mod"** flag is given, then only users listed in the **MODS** evnironment vairable will be able to 
execute the command. Note that command name is not given with a leading **" ! "** when creating the command, 
though to invoke any custom command the **" ! "** is needed, like:

    !kappa

#### !auth
This command initiates the autorization process for scopes that require elevated permission by calling the 
**authorize()** function. This permission is given by the owner of the Twitch channel the bot will be 
running for. This process must be completed before the **!essub** command will work.

The bot specifically uses the **OIDC authorization code flow** process https://dev.twitch.tv/docs/authentication/getting-tokens-oidc#oidc-authorization-code-flow. When called with this command, the **authorize()** function will 
compile the auth URL and print it out in the terminal as follows:

    AUTHORIZATION URL: https://id.twitch.tv/oauth2/authorize
        ?response_type=code'
        &client_id={client_id}'
        &redirect_uri={callback}'
        &scope=channel:read:subscriptions%20bits:read'
        &state={state}'

This URL needs to be shared with the person whose channel the bot will be joining, and once they visit the URL an 
authorization page will ask the user to sign up or log into Twitch and allow them to choose whether to authorize 
the bot to accesss their subscriber information.

Once authorization is given, no information about it needs to be (or is) remembered for the given scope(s) (in 
this case just **read:subscriptions** and **bits:read**). This behaviour can be changed by adding:

    &force_verify=true

to the URL within the **authorize()** function, and re-authorization will then be required occasionally.

If any additional scopes are added, they will need to be authorized as well. In practice, this command should 
very rarely need to be used, normally the first time the bot is added to a new channel.

#### !delcom
Deletes custom commands from the database if the command exists.

###### Usage:
    !delcom <command name>

    !delcom pogchamp

No confirmation is requested before deletion, so use with care. Note that all commands detailed here are hardcoded 
as they are needed for the core functionality of the bot, and as such are not stored in the database or removable.

#### !disconnect
This command triggers the **nuke_eventsubs()** function, posts a message in chat indicating that the bot is "going to 
sleep", shuts the HTTP server down, closes the connection to the SQLite database, and exits the Python script.

The **nukeeventsubs()** function is called because Twitch will occasionally send a request to the bot to check 
that it is still listening, and if the bot is offline for a while and does not respond the EventSub topics will 
be unsubscribed automatically. Clearing the subscriptions at time of disconnect cleans these up more gracefully 
than just letting them expire.

#### !esfollow
Subscribes the bot to the current channel's "follow" event, allowing the bot to be notified every time a user 
follows the channel. The bot will then thank every user that follows the channel while the EventSub is active.
This command must be used every time the bot rejoins the channel if the **!disconnect** command is used to shut it
down, as that command clears all EventSubs.

#### !essub
Very similar to **!esfollow**, this command subscribes to the current channel's "subscribe" and "cheer" topics, 
and allows the bot to thank subscribers and cheerers in the chat. The bot will mention the user's name and the 
tier (1-3) that they subbed at or the amound of bits they cheered, thanking them for the support. Anonymous
cheerers will be thanked as "Anonymous".

These topics requires additional permissions compared to "follow", and the command will not work until the 
**!auth** command has been run and the permission is obtained. This command must be used every time the bot 
rejoins the channel if the **!disconnect** command is used to shut it down, as that command clears all EventSubs.

#### !nukeeventsubs
This command will unsibscribe the bot from every EventSub topic it is subscribed to. As such, if the bot is 
subscribed to many topics, this command should be used with care, though currently the bot only has capability 
for "follow", "subscribe", and "cheer" EventSub topics implemented.

The function this command calls is intended as a cleanup function, because even failed EventSub subscriptions 
remain in Twitch's logs, and multiple EventSub subscriptions to the same topic can be inadvertently created.

#### !so
A shoutout command used for referring viewers to other Twitch streamers.

###### Usage:
    !so <twitch username>

    !so CohhCarnage

The bot will then post the following in the chat:

    Check out CohhCarnage at https://twitch.tv/CohhCarnage !

## Plans for the Future
I would like to add a lot more functionality to this bot, including the ability for the bot to play sound alerts 
to the streamer when someone in the chat says hi to them so they don't miss anyone's hellos in chat, and more 
EventSub alert topics like raids, channel point usage, and more. Currently EventSub follow, subscribe, and cheer
alerts are supported. 

Additional functionality is also planned for custom commands, including the ability to change the cooldown timer 
for commands from chat, and possibly allowing for individual commands to have their own cooldown times.

A refactor to switch more of the code to a more object-oriented approach may also be completed at some point, 
once I am more comfortable with that style of programming.
