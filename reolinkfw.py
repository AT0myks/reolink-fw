import asyncio
import json
from datetime import datetime

import aiohttp


async def getit(session, url):
    async with session.get(url) as resp:
        return await resp.json()


async def getem(urls):
    async with aiohttp.ClientSession() as session:
        return await asyncio.gather(*[getit(session, url) for url in urls])


def md_link(label, url):
    return f"[{label}]({url})"


def make_readme(dict_):
    text = ""
    for dev, val in dict_.items():
        text += "<details>\n  <summary>" + dev + "</summary>\n"
        for hv, firmwares in val["hardwareVersions"].items():
            text += "\n  ## " + hv + "\n"
            text += "Version | Date | Changes | Notes\n"
            text += "--- | --- | --- | ---\n"
            for f in firmwares:
                dl_url = f["url"] + '?download_name=' + f["filename"]
                version = md_link(f["version"], dl_url)
                date = datetime.fromtimestamp(f["displayTime"] / 1000).date()
                new = f["new"].replace("\n", '')
                notes = f["note"].replace("\n", '')
                text += " | ".join((version, "<nobr>" + str(date) + "</nobr>", new, notes)) + '\n'
        text += "\n</details>\n\n"
    return text


if __name__ == "__main__":
    urls = [f"https://reolink.com/wp-json/reo-v2/download/firmware/?dlProductId={id_}" for id_ in range(200)]
    results = []
    for response in asyncio.run(getem(urls)):
        if response is None:
            continue
        results.extend(response["data"])

    merged_dict = dict.fromkeys(sorted(r["title"] for r in results))
    for result in results:
        device = result["title"]
        if merged_dict[device] is None:
            merged_dict[device] = {
                "url": result["url"],
                "firmwares": result["firmwares"]
            }
        else:
            merged_dict[device]["firmwares"].extend(result["firmwares"])

    for val in merged_dict.values():
        hvs = {fw["hardwareVersion"][0]["title"]: [] for fw in val["firmwares"]}
        for fw in val["firmwares"]:
            hv = fw["hardwareVersion"][0]["title"]
            del fw["hardwareVersion"]
            if fw not in hvs[hv]:
                hvs[hv].append(fw)
        del val["firmwares"]
        val["hardwareVersions"] = hvs

    with open("reolink_fw.json", "w", encoding="utf8") as f:
        json.dump(merged_dict, f, indent=2, ensure_ascii=False)
    with open("README.md", "w", encoding="utf8") as f:
        f.write(make_readme(merged_dict))
