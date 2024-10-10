import random
import re
# import openai
import datetime
from dataset import patterns
import webbrowser as web
import speech_recognition as sr
import datetime
import pyttsx3
import requests
from bs4 import BeautifulSoup
from tabulate import tabulate
import speedtest
import psutil
import requests
import platform
import os
import cv2
import numpy as np
from fr2 import load_images

openai.api_key = "Your_Key"

# Define a function to generate responses based on user input

Name = "Zoe"
queryuser=""
def recognize_faces(img_paths, face_recognizer):
    """
    Recognize faces in a video stream and display name with bounding box
    """
    cap = cv2.VideoCapture(0)
    face_recognized=False
    while True:
        # Capture frame-by-frame
        ret, frame = cap.read()
        if not face_recognized:

            # Convert to grayscale
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            # Detect faces
            faces = cv2.CascadeClassifier('D://Projects//project AI//haarcascade_frontalface_default.xml').detectMultiScale(gray, scaleFactor=1.2, minNeighbors=5)

            # Recognize faces
            for (x, y, w, h) in faces:
                roi_gray = gray[y:y+h, x:x+w]
                label, confidence = face_recognizer.predict(roi_gray)
                if confidence < 100:
                    queryuser = os.path.splitext(os.path.basename(img_paths[label]))[0]
                    cv2.putText(frame, queryuser, (x, y-10), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2, cv2.LINE_AA)
                
                    face_recognized = True
                    break  # stop recognizing faces
                else:
                    cv2.putText(frame, 'Unknown', (x, y-10), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2, cv2.LINE_AA)

                cv2.rectangle(frame, (x, y), (x+w, y+h), (0, 255, 0), 2)
        else:
            say(queryuser)
            query=listen()
            # Command(query)
            face_recognized=False

        # Display the resulting frame
        cv2.imshow('Video', frame)

        # Exit loop if 'q' key is pressed
        if cv2.waitKey(1) & 0xFF == ord('q'):
            break

    # Release the capture
    cap.release()
    cv2.destroyAllWindows()

def listen():
    r = sr.Recognizer()
    while True:
        with sr.Microphone() as source:
            print("listening....")
            r.adjust_for_ambient_noise(source, duration=1)
            audio = r.listen(source, phrase_time_limit=8)
            try:
                print("Recognizing.....")
                query = r.recognize_google(audio, language="en-in")
                print(f"\nyou just said : {query}")
                return query.lower()
            except sr.UnknownValueError:
                say("Could not understand your command, Try Again")
            except sr.RequestError as e:
                say("Could not request results from Google Speech Recognition service : ")
                print(e)
            except sr.WaitTimeoutError:
                say("Listening timed out, please try again")

def say(Text):  # passsed
    try:
        engine = pyttsx3.init("sapi5")
        Voices = engine.getProperty('voices')
        engine.setProperty('voice', Voices[1].id)
        engine.setProperty('rate', 190)
        print(Name + ": " + Text)
        engine.say(text=queryuser+Text)
        engine.runAndWait()
    except pyttsx3.EngineError as e:
        print(f"Error: {e}")
        say("Engine Error, Sorry Try Again")
    except pyttsx3.RequestError as e:
        print(f"Error: {e}")
        say("Request Error, Sorry Try Again")

def wishMe():  # passsed

    hour = int(datetime.datetime.now().hour)
    if hour >= 0 and hour < 12:
        say("Good Morning...")

    elif hour >= 12 and hour < 18:
        say("Good Afternoon...")

    else:
        say("Good Evening ")

def internetConnection():  # passsed
    url = "https://www.youtube.com"
    timeout = 5
    try:
        request = requests.get(url, timeout=timeout)
        say("INTERNET connection Detected")
        st = speedtest.Speedtest()
        say("Checking Internet Speed, it will be fast")
        download_speed = st.download()
        upload_speed = st.upload()
        ping = st.results.ping
        say(f"Download speed: {download_speed / 1000000:.2f} Mbps")
        say(f"Upload speed: {upload_speed / 1000000:.2f} Mbps")
        say(f"Ping: {ping:.2f} ms")

    except (requests.ConnectionError, requests.Timeout) as exception:
        say("Current Device is Not connected to the internet")
        say("No INTERNET connection Detected")
        exit()

def temprature(query):  # passed
    # creating url and requests instance
    url = "https://www.google.com/search?q="+"weather"+query
    html = requests.get(url).content
    # getting raw data
    soup = BeautifulSoup(html, 'html.parser')
    temp = soup.find('div', attrs={'class': 'BNeawe iBp4i AP7Wnd'}).text
    str = soup.find('div', attrs={'class': 'BNeawe tAd8D AP7Wnd'}).text
    # formatting data
    data = str.split('\n')
    time = data[0]
    sky = data[1]
    # getting all div tag
    listdiv = soup.findAll('div', attrs={'class': 'BNeawe s3v9rd AP7Wnd'})
    strd = listdiv[5].text
    # getting other required data
    pos = strd.find('Wind')
    other_data = strd[pos:]
    # printing all data
    say("Temperature in " + query+" is " + temp)
    say("Time is : " + time)
    say("Sky Description is : " + sky)

def SystemSpecifications():  # passed
    # Computer network name
    say("Computer network name: "+platform.node())
    # Machine type
    say("Machine type: "+platform.machine())
    # Processor type
    say("Processor type: "+platform.processor())
    # Platform type
    say("Platform type: "+platform.platform())
    # Operating system
    say("Operating system: "+platform.system())
    # Operating system release
    say("Operating system release: "+platform.release())
    # Operating system version
    say("Operating system version: "+platform.version())
    # Physical cores
    say(str(psutil.cpu_count(logical=False))+": Physical Cores ")
    # Logical cores
    say(str(psutil.cpu_count(logical=True))+": Logical cores ")
    # Current frequency
    say("Current CPU frequency: "+str(psutil.cpu_freq().current))
    # Min frequency
    say("Min CPU frequency: "+str(psutil.cpu_freq().min))
    # Max frequency
    say("Max CPU frequency: "+str(psutil.cpu_freq().max))

def StorageDetails():  # passed
    partitions = psutil.disk_partitions()
    for partition in partitions:
        say(f"Device: {partition.device}")
        say(f"Mountpoint: {partition.mountpoint}")
        say(f"File system type: {partition.fstype}")
        try:
            partition_usage = psutil.disk_usage(partition.mountpoint)
        except PermissionError:
            continue
        say(
            f"Total size: {partition_usage.total / (1024*1024*1024):.2f} GB")
        say(f"Used: {partition_usage.used / (1024*1024*1024):.2f} GB")
        say(f"Free: {partition_usage.free / (1024*1024*1024):.2f} GB")
        say(f"Percentage used: {partition_usage.percent}%")

def intro():  # passsed

    say(" Your Virtual Assistant is Online Sir")
    wishMe()
    temprature("islamabad")
    SystemSpecifications()
    say("Checking Protocols")
    internetConnection()
    #systemUsage()
    StorageDetails()
    say("All systems have been Activated")

def startup():
    say(" Your Virtual Assistant "+Name+" is Online Sir")
    wishMe()
    temprature("islamabad")
    say("Checking Protocols")
    internetConnection()
    #systemUsage()
    say("All systems have been Activated")

def chatgpt_codeGeneration(prompt):
    response = openai.Completion.create(
        engine="davinci-codex",
        prompt=prompt,
        max_tokens=300,
        n=1,
        stop=None,
        temperature=0.5,
    )
    print(response.choices[0].text)

def generate_responses(prompt):
    response = openai.Completion.create(
        engine= "text-davinci-003",
        prompt=prompt,
        max_tokens=4000,
        
        stop= None,
        temperature=0.5
        )
    return response["choices"][0]["text"]

def generate_images(prompt):#openAI Image Generator
    try:
        response = openai.Image.create(
            prompt=prompt,
            n=3,
            size="1024x1024",
            model="image-alpha-001",
        )
        best_image = max(response["data"], key=lambda x: x["score"])
        url = best_image["url"]
        web.open(url)
    except openai.Error as e:
        print(f"OpenAI Error: {e}")
    except web.Error as e:
        print(f"Web Error: {e}")

def image(prompt):#DeepAI Image Generator
    try:
        response = requests.post(
            "https://api.deepai.org/api/text2img",
            data={
                'text': prompt,
            },
            headers={'api-key': 'quickstart-QUdJIGlzIGNvbWluZy4uLi4K'},
        )
        if response.status_code == 200:
            url = response.json()['output_url']
            web.open(url)
        else:
            print(f"Error: {response.text}")
    except requests.exceptions.RequestException as e:
        print(f"Error: {e}")

def play_music( query):
    music_dir = "D://SnapTube Audio"
    songs = [filename for filename in os.listdir(music_dir) if filename.endswith(".mp3")]
    song_found = False
    for song in songs:
        if query.lower() in song.lower():
            song_found = True
            say(f"Playing {song}")
            os.startfile(os.path.join(music_dir, song))
            break

    # If the song was not found, inform the user
    if not song_found:
        say(f"Sorry, I couldn't find {query} in the music directory.")
