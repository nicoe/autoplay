#!/usr/bin/env python2
# -*- coding: utf-8 -*-

'''
Keeps your MPD playlist filled with music you like

Dependencies : python-mpd
               pysqlite
'''

import os
import mpd
import random
import sqlite3
import time
import io
import sys
from socket import error as socketerror
import signal

## Config
trigger = 8 # A new song will be added when the playlist
            #  has less songs than this
            #  You can set this to 0 if you only want the stats
playtime = 70 # Percentage of a song that must be played before
              #  play count is incremented
mintime = 25 # Minimum length of a track for it
             #  to be considered a song (in seconds)
flood_delay = 12*60 # Minutes to wait before adding the same song again
mindelay = 0.1 # These are the min and max polling delays
maxdelay = 1.0 # These values should be sane for pretty much any
               # remotely recent computer. Increase them if cpu usage
               # is too high
tries = 10 # Retry connecting this many times

logfile = "/tmp/autoplay.log"
## /Config

#enc = sys.getfilesystemencoding()
enc = "UTF-8"

## Functions
def log(msg, stdout=False):
  """Logs to file, and optionally to stdout. Obvious enough"""
  if stdout:
    print msg[2:]
  logio.write(unicode(msg, enc)+"\n")

def connect(i=1):
  log("N Connecting...")
  if i == tries:
    log("E Could not connect to server D:", stdout=True)
    exit(1)
  try:
    client.connect(host, port)
  except socketerror:
    log("N Try n°"+str(i)+" failed")
    time.sleep(i*3)
    connect(i+1)
  log("N Connected")


def addsong():
  """Adds a semi-random song to the playlist"""
  rand = random.uniform(-0.5, 2)
  cursor.execute("select * from songs where karma>? and time < ?\
      ORDER BY random() LIMIT 1;",
      (rand, int(time.time()-(60*(flood_delay-trigger*3)))))
  data = cursor.fetchall()
  if data == []:
    updateone()
    addsong()
  else:
    songdata = data[0]
    newkarma = karma(songdata, 2)
    cursor.execute(
        "update songs set added=?, karma=?, time=? where file=?",
        (songdata[2]+1, newkarma, int(time.time()), songdata[0],)
        )
    db.commit()
    client.add(songdata[0].encode(enc))

def getsong(songfile):
  """Retrieve song data from DB"""
  cursor.execute("select * from songs where file=?", (songfile,))
  data = cursor.fetchone()
  if data == None:
    cursor.execute("insert into songs values (?, 0, 0, 0.5, 0)",
        (songfile,))
    data = (songfile, 0, 0, 0.5, 0)
  return data

def karma(songdata, which=0):
  """Returns karma for a song"""
  listened = float(songdata[1])
  added = float(songdata[2])

  if which == 1:
    listened += 1
  elif which == 2:
    added += 1

  if listened == 0:
    listened = 0.1
  if added == 0:
    added = 0.1
  return listened/added

def listened(songdata):
  newkarma = karma(songdata, 1)
  cursor.execute(
      "update songs set listened=?, karma=?, time=? where file=?",
      (songdata[1]+1, newkarma, int(time.time()), songdata[0])
      )
  db.commit()
## /Functions

allsongs = []
def updateone():
  if allsongs == []:
    for song in client.list("file"):
      allsongs.append(unicode(song, enc))
    for song in cursor.execute("select file from songs;"):
      allsongs.append(song[0])
    random.shuffle(allsongs)

  song = allsongs.pop()
  # Check if the file is in DB
  cursor.execute("select * from songs where file=?", (song,))
  if cursor.fetchone() == None:
    cursor.execute("insert into songs values (?, 0, 0, 5, 0);",
        (song,))
    db.commit()

  # Check if the file is in mpd
  if len(client.search("filename", song.encode(enc))) == 0:
    log("D "+song.encode(enc)+" doesn't exist?")
    cursor.execute("delete from songs where file=?", (song,))
    db.commit()

def getSetting(name):
  cursor.execute("""SELECT value FROM setting
      WHERE name = ?;""", (name,))
  one = cursor.fetchone()
  if not one: return None
  return one[0]

def setSetting(name, val):
  cursor.execute("""INSERT INTO setting (name, value)
      VALUES (?, ?);""", (name, val))
  db.commit()

def initDB():
    cursor.execute("""CREATE TABLE IF NOT EXISTS setting(
        name text not null,
        value text
        );""")
    cursor.execute("""CREATE TABLE IF NOT EXISTS songs(
        file text,
        listened int,
        added int,
        karma real,
        time int
        );""")
    db.commit()
    dbversion = getSetting("dbversion")
    if not dbversion:
      setSetting("dbversion", "1")
    #if int(dbversion) < 2: blah blah upgrade
    #db.commit()

def serve():
  global client, db, cursor

  fifo = os.fdopen(os.open(datahome + "/fifo",
              os.O_RDONLY | os.O_NONBLOCK))

  db = sqlite3.connect((datahome+"/db.sqlite").encode(enc))
  cursor = db.cursor()
  initDB()
  cursor.execute("VACUUM;")


  random.seed()
  client = mpd.MPDClient()
  connect()

  if password:
    try:
      log("D Using password")
      client.password(password)
    except mpd.CommandError:
      log("E Couldn't connect. Wrong password?", stdout=True)
      exit(2)

  for i in range(5):
    updateone()

  armed = True
  delay = mindelay
  radioMode = True

  log("N Ready")

  while True:

    try:
      updateone()
      if radioMode:
        if client.status()["consume"] == "0":
          cursongid = client.status()["songid"]
          for song in client.playlistid():
            if song["id"] == cursongid:
              neededlength = int(song["pos"]) + trigger
        else:
          neededlength = trigger
        if len(client.playlist()) < neededlength:
          addsong()
          delay = mindelay

      if client.status()['state'] == "play":
        times = client.status()['time'].split(":")
        pos = int(times[0])
        end = int(times[1])
        currentsong = client.currentsong()
        if not armed and "id" in currentsong and not songid == currentsong["id"]:
          armed = True
        elif armed and (end > mintime) and (pos > playtime*end/100):
          armed = False # Disarm until the next song
          listened(getsong(unicode(currentsong["file"], enc)))
          songid = (currentsong["id"])

    except KeyError:
      pass

    except (socketerror, mpd.ConnectionError):
      log("W Connection to MPD lost")
      client.disconnect()
      connect()

    comm = fifo.readline()
    if len(comm) != 0:
      delay = mindelay
      if comm == "kill\n":
        client.close()
        os.unlink(datahome + "/fifo")
        os.unlink(datahome + "/pid")
        log("N Quit")
        exit(0)
      elif comm == "stop\n":
        radioMode = False
        log("D Radio mode disabled")
      elif comm == "start\n":
        radioMode = True
        log("D Radio mode enabled")
      elif comm == "toggle\n":
        radioMode = not radioMode
        log("D Radio mode toggled")
      else: log("W Unknown command : " + comm[:-1])


    time.sleep(delay)
    delay = min((delay*1.1, maxdelay))


def getServFifo():
  try:
    pidf = open(datahome + "/pid") #IOError
    pid = pidf.read()
    pidf.close()
    os.kill(int(pid), 0) #OSError on kill, ValueError on int
  except (IOError, OSError, ValueError):
    log("N Starting server...", True)
    try:
      os.mkfifo(datahome + "/fifo")
    except OSError:
      pass
    pid = os.fork()
    if pid == 0:
      serve()
    pidf = open(datahome + "/pid", "w")
    pidf.write(str(pid))
    pidf.close()

  f = open(datahome + "/fifo", "w+")
  return f



datahome = (os.getenv("XDG_DATA_HOME") or os.getenv("HOME") +
            "/.local/share") + "/autoplay"
if not os.access(datahome, os.W_OK):
  try:
    os.makedirs(datahome)
  except os.error:
    log("E Couldn't access nor create" + datahome + ", quitting", True)
    exit(2)

password = None

host = os.getenv("MPD_HOST", "127.0.0.1")
atloc = host.find("@")
if(atloc != -1):
  password = host[:atloc]
  host = host[atloc+1:]

port = os.getenv("MPD_PORT", "6600")
#musicdir = os.getenv("MPD_MUSIC_DIR") or os.getenv("mpd_music_dir")


logio = io.open(logfile, "at", buffering=1, encoding=enc)



fifo = getServFifo()
if len(sys.argv) > 1:
  fifo.write(" ".join(sys.argv[1:]) + "\n")

fifo.close()

# vim: tw=70 ts=2 sw=2
