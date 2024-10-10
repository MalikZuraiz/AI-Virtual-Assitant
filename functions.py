from ast import Pass
from email.headerregistry import Address
from typing import Counter
from Zoemain import sophie as s
import wikipedia
from pywikihow import RandomHowTo, search_wikihow
import webbrowser as web
import pywhatkit
import os
from bs4 import BeautifulSoup
import pyjokes
from googletrans import Translator, constants
import platform
import psutil
import GPUtil
from tabulate import tabulate
import speedtest 
import requests

# def sendEmail(to, content):
#         server = smtplib.SMTP('smtp.gmail.com', 587)
#         server.ehlo()
#         server.starttls()
#         server.login('youremail@gmail.com', 'your-password')
#         server.sendmail('youremail@gmail.com', to, content)
#         server.close()
        
def GoogleSearch(term):                                        #passed
    query = term.replace("where are","")
    query = query.replace("where is","")
    query = query.replace("how to","")
    query = query.replace("what is","")
    query = query.replace(" ","")
    query = query.replace("what is meant by","")
    writeab = str(query)
    Query = str(term)
    pywhatkit.search(Query)
    search = wikipedia.summary(Query,2)
    s.say(f": According To Your Search : {search}")
            
def YouTubeSearch(query):                                       #passed
    result = "https://www.youtube.com/results?search_query=" + query
    web.open(result)
    s.say("This Is What I Found For Your Search .")
    pywhatkit.playonyt(query)
    s.say("This May Also Help You Sir .")

def wikipediaFunc(query):                                       #passed 
    s.say('Searching Wikipedia...')
    try:
        query = query.replace("wikipedia", "")
        results = wikipedia.summary(query, 3)
        s.say("According to Wikipedia : ")
        s.say(results)
    except: 
        s.say("i couldn't exactly get what you want. try again ")

def openApps(query):                                            #passed
    try:
        codePath = "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe" 
        os.startfile(codePath)
        s.say("Opening"+query)
    except :
        s.say("file can't be found")
    
def howToQuestion(query):                                       #passed
        s.say('getting data from internet ')
        op=query.replace("sophi","")
        max_result=1
        how_to_func=search_wikihow(op,max_result)
        assert len(how_to_func)==1
        #how_to_func[0].print()
        s.say(how_to_func[0].summary)  

def programmingJoke():                                            #passed
    joke=pyjokes.get_joke(language="en",category="neutral")
    s.say(joke)
    
def allJokeCracker():#                                             passed
    joke=pyjokes.get_joke(language="en",category="all")
    s.say(joke)
    
def jokeCrackerChuck():                                            #passed
    joke=pyjokes.get_joke(language="en",category="chuck")
    s.say(joke)
    
def google_translator(line):                                       #passed
    translator=Translator()
    translation = translator.translate(line,dest="en")
    # print(f"{translation.origin} ({translation.src}) --> {translation.text} ({translation.dest})")
    s.say("given input : " +translation.origin)
    s.say("Translated ouput :"+translation.text)

def languageDetector(line):                                        #passed
    
    translator=Translator()
    detection = translator.detect(line)
    print(line)
    google_translator(line)
    s.say("Language code:"+detection.lang)
    s.say("Language:" + constants.LANGUAGES[detection.lang])
    s.say("Confidence:"+str(detection.confidence))

