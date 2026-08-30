import sys
import trio
import httpx
import json
from holehe.core import import_submodules, get_functions, launch_module

async def main():
    if len(sys.argv) < 2:
        print("[]")
        return
        
    email = sys.argv[1]
    modules = import_submodules("holehe.modules")
    
    class Args:
        pass
    args = Args()
    args.onlyused = True
    args.nopasswordrecovery = False
    websites = get_functions(modules, args)
    
    client = httpx.AsyncClient(timeout=10)
    out = []
    
    # We do a fast scan
    async with trio.open_nursery() as nursery:
        for website in websites:
            nursery.start_soon(launch_module, website, email, client, out)
            
    await client.aclose()
    
    used_sites = [item['name'] for item in out if item.get('exists')]
    print(json.dumps(used_sites))

if __name__ == "__main__":
    try:
        trio.run(main)
    except Exception:
        print("[]")
