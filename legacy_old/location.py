from genericpath import getsize
from ipaddress import ip_address
from turtle import distance
from geopy.geocoders import Nominatim 
from Zoemain import sophie as s
import geopy.distance
import requests
import geopy

class location():
    def findLocation(self,value):                                         #passed
        # calling the Nominatim tool
        loc = Nominatim(user_agent="GetLoc")
        
        # entering the location name
        getLoc = loc.geocode(value)
        
        # printing address
        s.say(getLoc.address)
        
        # printing latitude and longitude
        lat=str(getLoc.latitude)
        lon=str(getLoc.longitude)
        s.say("Latitude = "+ lat)
        s.say(" and Longitude = " + lon)
        
    def findLocation_lat_lon(value):                                  #passed
        # calling the nominatim tool
        geoLoc = Nominatim(user_agent="GetLoc")
        # passing the coordinates
        locname = geoLoc.reverse(value)
        # printing the address/location name
        s.say(locname.address)

    def get_ip(self):                                                    #passed
        response = requests.get('https://api64.ipify.org?format=json').json()
        return response["ip"]

    def myLocation(self):                                                #passed
        ip_address = self.get_ip()
        response = requests.get(f'https://ipapi.co/{ip_address}/json/').json()
        location_data = {
            "ip": ip_address,
            "city": response.get("city"),
            "region": response.get("region"),
            "region_code": response.get("region_code"),
            "region": response.get("region"),
            "country": response.get("country_name"),
            "country_code": response.get("country_code"),
            "country_capital": response.get("country_capital"),
            "latitude": response.get("latitude"),
            "longitude": response.get("longitude"),
            "timezone": response.get("timezone"),
            "country_calling_code": response.get("country_calling_code"),
            "languages": response.get("languages"),
            "country_population": response.get("country_population"),
            "country_area": response.get("country_area")
        }
        s.say( location_data)

    def GoogleMaps(place1,place2):                                    #passed
        loc = Nominatim(user_agent="GetLoc")
        getLoc = loc.geocode(place1)
        lat1=str(getLoc.latitude)
        lon1=str(getLoc.longitude)
        getLoc = loc.geocode(place2)
        lat2=getLoc.latitude
        lon2=getLoc.longitude
        p_1 = (lat1 , lon1 ) 
        p_2 = (lat2 , lon2 ) 
        d = round(geopy.distance.geodesic (p_1, p_2).km) 
        s.say("distance from "+place1+" to "+place2+" is "+str(d)+" kilometers")


l=location()
s.say(l.get_ip())