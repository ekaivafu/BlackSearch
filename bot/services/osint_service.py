import asyncio
import json
import os

class OSINTService:
    @staticmethod
    async def check_email_holehe(email: str) -> dict:
        """
        Runs the holehe python script in a separate process to avoid trio vs asyncio conflicts.
        Returns a dict:
          {
            "found":         ["discord", ...],   # confirmed registered
            "blocked":       ["instagram", ...], # couldn't check (CAPTCHA/firewall)
            "checked_count": 55,
            "blocked_count": 12,
          }
        """
        empty = {"found": [], "blocked": [], "checked_count": 0, "blocked_count": 0}
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
            
            # Find the first '{' (new format) or '[' (old format) and parse from there.
            output_str = stdout.decode('utf-8').strip()
            
            try:
                # New format: JSON object
                start_idx = output_str.find('{')
                if start_idx != -1:
                    data = json.loads(output_str[start_idx:])
                    if isinstance(data, dict) and "found" in data:
                        return data
                
                # Fallback: old plain list format
                start_idx = output_str.find('[')
                if start_idx != -1:
                    sites = json.loads(output_str[start_idx:])
                    if isinstance(sites, list):
                        return {**empty, "found": sorted(sites)}
            except Exception:
                pass
                
            return empty
            
        except Exception as e:
            print(f"Holehe OSINT Error: {e}")
            return empty

    @staticmethod
    async def search_username_sherlock(username: str) -> list[dict]:
        """
        Runs sherlock as a subprocess to find active username profiles.
        Returns a list of dicts with 'site' and 'url'.
        """
        import re
        try:
            # Run sherlock for max 60 seconds, read line by line to prevent buffering loss
            env = os.environ.copy()
            env["PYTHONUNBUFFERED"] = "1"
            process = await asyncio.create_subprocess_exec(
                "sherlock", username, "--timeout", "5", "--print-found", "--no-color",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=env
            )
            
            results = []
            ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
            
            async def read_lines():
                while True:
                    line = await process.stdout.readline()
                    if not line:
                        break
                    line_str = line.decode('utf-8', errors='ignore')
                    line_str = ansi_escape.sub('', line_str)
                    matches = re.findall(r"\[\+\] (.*?):\s+(https?://[^\s]+)", line_str)
                    for site, url in matches:
                        results.append({"site": site.strip(), "url": url.strip()})
            
            # We don't want it to run forever and block the bot
            try:
                await asyncio.wait_for(read_lines(), timeout=60.0)
            except asyncio.TimeoutError:
                process.terminate()
                
            return sorted(results, key=lambda x: x["site"])
            
        except Exception as e:
            print(f"Sherlock OSINT Error: {e}")
            return []
