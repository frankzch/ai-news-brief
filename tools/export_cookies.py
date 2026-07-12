
import os
import json
import logging
from playwright.sync_api import sync_playwright

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def export_cookies():
    # Define paths
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    profile_dir = os.path.join(base_dir, 'data', 'playwright_profile')
    output_file = os.path.join(base_dir, 'data', 'cookies.json')
    
    if not os.path.exists(profile_dir):
        logging.error(f"Profile directory not found: {profile_dir}")
        logging.error("Please run the application locally first to generate the profile and log in.")
        return

    logging.info(f"Using profile directory: {profile_dir}")
    
    with sync_playwright() as p:
        # Launch persistent context with the existing profile
        try:
            # Try launching with Chrome first
            logging.info("Launching browser (Chrome)...")
            context = p.chromium.launch_persistent_context(
                user_data_dir=profile_dir,
                channel='chrome',
                headless=False, # Show browser to verify if needed
            )
        except Exception as e:
            logging.warning(f"Failed to launch Chrome: {e}")
            try:
                # Fallback to Edge
                logging.info("Launching browser (Edge)...")
                context = p.chromium.launch_persistent_context(
                    user_data_dir=profile_dir,
                    channel='msedge',
                    headless=False,
                )
            except Exception as e2:
                logging.error(f"Failed to launch Edge: {e2}")
                return

        try:
            page = context.pages[0] if context.pages else context.new_page()
            
            # Navigate to Reddit to ensure we get relevant cookies
            logging.info("Navigating to reddit.com...")
            page.goto("https://www.reddit.com", wait_until="domcontentloaded")
            page.wait_for_timeout(3000) # Wait a bit for everything to settle
            
            # Export cookies
            cookies = context.cookies()
            logging.info(f"Found {len(cookies)} cookies.")
            
            # Filter for Reddit cookies (optional, but good for hygiene)
            reddit_cookies = [c for c in cookies if 'reddit' in c['domain']]
            logging.info(f"Found {len(reddit_cookies)} Reddit-specific cookies.")
            
            # Save ALL cookies to ensure session is preserved completely
            with open(output_file, 'w') as f:
                json.dump(cookies, f, indent=2)
            
            logging.info(f"Cookies exported successfully to: {output_file}")
            logging.info("You can now upload this file to your cloud server's 'data' directory.")
            
        except Exception as e:
            logging.error(f"Error during export: {e}")
        finally:
            context.close()

if __name__ == "__main__":
    export_cookies()
