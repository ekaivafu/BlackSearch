import asyncio
import json
import os

class OSINTService:
    @staticmethod
    async def check_email_holehe(email: str) -> list[str]:
        """
        Runs the holehe python script in a separate process to avoid trio vs asyncio conflicts.
        Returns a list of website names where the email is registered.
        """
        try:
            # Get path to the holehe script
            script_path = os.path.join(os.path.dirname(__file__), "..", "scripts", "run_holehe.py")
            
            # Run the script as a subprocess
            process = await asyncio.create_subprocess_exec(
                "python", script_path, email,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            stdout, stderr = await process.communicate()
            
            # Output might contain warnings before the JSON array.
            # We can find the first '[' and parse from there.
            output_str = stdout.decode('utf-8').strip()
            
            try:
                start_idx = output_str.find('[')
                if start_idx != -1:
                    json_str = output_str[start_idx:]
                    sites = json.loads(json_str)
                    return sorted(sites)
            except Exception:
                pass
                
            return []
            
        except Exception as e:
            print(f"Holehe OSINT Error: {e}")
            return []

    @staticmethod
    async def search_username_sherlock(username: str) -> list[dict]:
        """
        Runs sherlock as a subprocess to find active username profiles.
        Returns a list of dicts with 'site' and 'url'.
        """
        import re
        try:
            # Run sherlock for max 20 seconds
            process = await asyncio.create_subprocess_exec(
                "sherlock", username, "--timeout", "3", "--print-found",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            # We don't want it to run forever and block the bot
            try:
                stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=30.0)
            except asyncio.TimeoutError:
                process.terminate()
                stdout, stderr = await process.communicate()
            
            output = stdout.decode('utf-8')
            
            # Regex to match "[+] Site: https://..."
            matches = re.findall(r"\[\+\] (.*?):\s+(https?://[^\s]+)", output)
            
            results = []
            for site, url in matches:
                results.append({"site": site.strip(), "url": url.strip()})
                    
            return sorted(results, key=lambda x: x["site"])
            
        except Exception as e:
            print(f"Sherlock OSINT Error: {e}")
            return []
