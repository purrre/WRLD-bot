## WRLD

WRLD (changed from wrld -> WRLD), rebuilt. this is the open-source version of my Juice WRLD discord bot, cleaned up and
made easy to self-host. it pulls song, leak, lyric, cover and groupbuy data from
[juiceWRLDapi.com](https://juiceWRLDapi.com) and a couple of my own services, and serves it 
through text and slash commands. no more needing to rely on spammy bots like moonlight, canary, hh wrld, etc etc. You can build this and only need to rely on yourself!

some practices i use are a little overkill for what the bot is, but thats because this is the base of a great project to come in the future..

it's free to use, and it'll stay that way. if you just want the public bot you can invite it
and move on. if you want to run your own copy, keep reading. if you're new to python dont bother cause i dont want to
help you

this open sourced version includes slightly less features than my running one (file tagging, ai commands, dbadmin) because they are too reliant on my own services. this open source version is purely reliant on only my own `wrld.pure0.lol` endpoints. juicewrldapi is much more reliable than i am

## what changed from the old version

i dont wanna type that much but its a lot more stable, better looking, and lots more features

still built on [pycord](https://pycord.dev/), using components v2

## requirements

- python 3.12+
- a redis server
- pycord 2.7.0+

## setup

1. install the deps:

```bash
pip install -r requirements.txt
```

2. create a `.env` file in the project root and paste in the following, then fill it in:

```env
# required
BOT_TOKEN=
REDIS_URL=redis://localhost:6379/0

# optional
DEV_MODE=false
DEV_BOT_TOKEN=
DEFAULT_PREFIX=,
ADMIN_USERS=
DATABASE_URL=sqlite:///data/osse.db
SYNC_ENABLED=true
SYNC_INTERVAL=300
```

at minimum set `BOT_TOKEN` and a working `REDIS_URL`. everything else has sane defaults.

3. start it:

```bash
python main.py
```

on boot it sets up the schema, hydrates the cache from redis (and does a full sync from the api
if redis is empty), then connects.

## voice / music player

WRLD doesn't have voice channel features built in, but i built a separate media player thats very simple to run. if you want to play Juice WRLD in voice channels, check out
[WRLD-ext-media-player](https://github.com/purrre/WRLD-ext-media-player) build and run it for yourself.

## the database

sqlite via sqlalchemy. the models are in `utils/database.py` and the schema is created
automatically on first run.

## the cache

redis stores the api data (songs, albums, eras, groupbuys). each dataset is hashed, so a poll
that finds nothing changed does zero writes. derived data (titles index and lyrics map) is keyed
off the songs hash and only rebuilds when songs actually change. `/recache` forces a full
refresh. if `SYNC_ENABLED` is true, a background poller hits the api every `SYNC_INTERVAL`
seconds and keeps everything up to date automatically.

## fair use

use this to build your own bot, extend it, run it publicly or privately, whatever. the data it
pulls belongs to its respective owners and should be treated with respect. distributed files
stay the property of the original producers, artists and engineers.

questions or anything else, find me on discord `@purree`.

## credits

- JuiceWRLDAPI
- gabedoesntgaf GB/PB tracker
- my own services (wrld.pure0.lol, tagger.pure0.lol)