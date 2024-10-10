from chatbot import say , image , chatgpt_codeGeneration, intro, startup, SystemSpecifications,temprature, play_music, generate_responses, listen, emotionDetector
from automation import YouTube,Windows,Chrome
from functions import *
from VM import vm_protocol
import os
import datetime

def Command(query):

    if "bye".lower() in query.lower() or "goodbye".lower() in query.lower():
        exit()

    elif "what is the time".lower() in query.lower() or "what's the time".lower() in query.lower() or "what time is it".lower() in query.lower():
        strTime = datetime.datetime.now().strftime("%H:%M:%S")
        say(f"Sir, the time is {strTime}")

    elif "generate image".lower() in query.lower()  or "image generation".lower() in query.lower():
        image(query)

    elif "generate Code".lower() in query.lower() or"code generation".lower() in query.lower():
        chatgpt_codeGeneration(query)

    elif "give me intro".lower() in query.lower() or "introduce yourself".lower() in query.lower():
        intro()

    elif "complete system details".lower() in query.lower() or "all system details".lower() in query.lower() or "complete system specification".lower() in query.lower():
        startup()

    elif "system details".lower() in query.lower() or "system specification".lower() in query.lower():
        SystemSpecifications()

    elif "system usage".lower() in query.lower() or "usage of system".lower() in query.lower():
        #systemUsage()
        pass

    elif "temperature in".lower() in query.lower() or "temperature of".lower() in query.lower():
        query=query.replace("temperature in","")
        temprature(query)

    elif "shutdown pc".lower() in query.lower() or "shutdown my pc".lower() in query.lower() or "pc shutdown".lower() in query.lower():
        os.system("shutdown /s")

    elif "restart pc".lower() in query.lower() or "restart my pc".lower() in query.lower() or "pc restart".lower() in query.lower():
        os.system("shutdown /r")

    elif "play".lower() in query.lower():
        query=query.replace("play","")
        play_music(query)

    elif "go to sleep".lower() in query.lower() or "go offline".lower() in query.lower() or "bye".lower() in query.lower() or "goodbye".lower() in query.lower():
        exit()
  
    elif "chatgpt".lower() in query.lower():
        say(generate_responses(query))
    
    elif "chrome".lower() in query.lower():
        query=query.replace("chrome","")
        Chrome(query)

    elif "window".lower() in query.lower():
        query=query.replace("window","")
        Windows(query)

    elif "youtube".lower() in query.lower():
        query=query.replace("youtube","")
        YouTube(query)
    
    elif "enable vm protocol".lower() in query.lower() or "enable virtual mouse protocol".lower() in query.lower():
        query=query.replace("youtube","")
        YouTube(query)
    
    elif "google search".lower() in query.lower():
        query=query.replace("google search","")
        GoogleSearch(query)

    elif "youtube search".lower() in query.lower():
        query=query.replace("youtube search","")
        YouTubeSearch(query)

    elif "wikipedia".lower() in query.lower():
        query=query.replace("wikipedia","")
        wikipediaFunc(query)
    
    elif "open".lower() in query.lower() :
        query=query.replace("open","")
        openApps(query)
    
    elif "programming jokes".lower() in query.lower():
        programmingJoke()
    
    elif "tell me jokes".lower() in query.lower():
        allJokeCracker()

    elif "chuck jokes".lower() in query.lower():
        jokeCrackerChuck()

    elif "google translator".lower() in query.lower():
        line = input("Enter the line to be translated: ")
        google_translator(query)

    elif "detect language".lower() in query.lower():
        line = input("Enter the line to detect the language: ")
        languageDetector(line)
    
    elif "find".lower() in query.lower():
       say(findLocation(line))

    elif "find latitude longitude of".lower() in query.lower():
        query=query.replace("find latitude logitude of","")
        findLocation_lat_lon(query)

    elif "my ip".lower() in query.lower():
        say(get_ip())

    elif "my location".lower() in query.lower():
        myLocation()
    
    elif "find distance between".lower() in query.lower():
       place1 = input("Enter the 1st Place: ")
       place2 = input("Enter the 2nd Place: ")
       GoogleMaps(place1,place2)

    else:
        say("no such command")


query= "i am happy give me system details"
# Command(query)
emotionDetector(query)
