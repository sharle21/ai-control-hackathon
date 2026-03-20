import os
import requests
import time
from dotenv import load_dotenv

load_dotenv()
API_KEY = os.getenv("LAMBDA_API_KEY")

def check_all_availability():
    url = "https://cloud.lambdalabs.com/api/v1/instance-types"
    try:
        response = requests.get(url, auth=(API_KEY, ""))
        if response.status_code != 200:
            print(f"Error: {response.status_code} - {response.text}")
            return False

        data = response.json().get("data", {})
        any_found = False

        for instance_name, details in data.items():
            regions = details.get("regions_with_capacity_available", [])
            
            if regions:
                region_names = [r['name'] for r in regions]
                print(f"✨ AVAILABLE: {instance_name} in {', '.join(region_names)}")
                any_found = True
        
        if not any_found:
            print(f"[{time.strftime('%H:%M:%S')}] Everything is still sold out...")
            
        return any_found
            
    except Exception as e:
        print(f"Connection error: {e}")
        return False

if __name__ == "__main__":
    print("🚀 Monitoring ALL Lambda instances... (Press Ctrl+C to stop)")
    while True:
        # We don't 'break' here so it keeps monitoring even after finding one
        check_all_availability()
        time.sleep(30) # Check every 30 seconds