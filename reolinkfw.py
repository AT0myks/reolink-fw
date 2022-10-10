import asyncio
import re
from datetime import datetime
from zoneinfo import ZoneInfo

import aiohttp
from dateutil.parser import parse as dtparse
from lxml.html import document_fromstring, fragment_fromstring, tostring
from lxml.html.builder import OL, LI
from waybackpy import WaybackMachineCDXServerAPI


async def get_one(session, url, type_):
    async with session.get(url) as resp:
        return await (resp.json() if type_ == "json" else resp.text())


async def get_all(urls, type_, limit_per_host=0):
    conn = aiohttp.TCPConnector(limit_per_host=limit_per_host)
    async with aiohttp.ClientSession(connector=conn) as session:
        return await asyncio.gather(*[get_one(session, url, type_) for url in urls])


def md_link(label, url):
    return f"[{label}]({url})"


def make_changes(changes):
    items = []
    subitems = []
    for idx, i in enumerate(changes):
        if i[0].isdigit() or i[0].isupper():
            items.append(LI(re.sub("^[0-9\s\W]{2,4}", '', i)))
        elif i[0].islower():
            subitems.append(LI(re.sub("^[a-z\s\W]{2,3}", '', i)))
            if (idx + 1) == len(changes) or not changes[idx + 1][0].islower():  # If end of list or next item is not a subitem.
                items[-1].append(OL(*subitems, type='a'))
                subitems = []  # Reset.
    return tostring(OL(*items)).decode()


def make_readme(firmwares):
    text = ''
    models = sorted(set(fw["model"] for fw in firmwares))
    for model in models:
        text += "<details>\n  <summary>" + model + "</summary>\n"
        model_fw = [fw for fw in firmwares if fw["model"] == model]
        hw_vers = sorted(set(fw["hw_ver"] for fw in model_fw))
        for hv in hw_vers:
            text += "\n  ### " + hv + "\n"
            text += "Version | Date | Changes | Notes\n"
            text += "--- | --- | --- | ---\n"
            for fw in (f for f in model_fw if f["hw_ver"] == hv): #sort by date
                if "filename" in fw:
                    dl_url = fw["url"] + '?download_name=' + fw["filename"]
                else:
                    dl_url = fw["url"]
                version = md_link(fw["version"], dl_url)
                if isinstance(fw["display_time"], str):
                    dt = datetime.fromisoformat(fw["display_time"]).date()
                else:
                    dt = fw["display_time"].date()
                date_str = str(dt).replace('-', chr(0x2011))
                if "changelog" in fw:
                    new = make_changes(fw["changelog"]) if len(fw["changelog"]) > 1 else fw["changelog"][0]
                else:
                    new = ''
                notes = fw.get("note", '').replace("\n", '')
                text += " | ".join((version, date_str, new, notes)) + '\n'
        text += "\n</details>\n\n"
    return text


def sanitize(string):
    return string.translate({
        160: ' ',  # \xa0
        183: '',  # \u00b7
        8217: "'",  # \u2019
        8220: '"',  # \u201c
        8221: '"',  # \u201d
    }).strip()


def parse_changes(text):
    text = sanitize(text)
    text = text.removeprefix("<p>").removesuffix("</p>")
    text = text.removeprefix("<P>").removesuffix("</P>")
    return re.split("\s*</?[pP]>\s*<[pP]>|\s*<br />\s*", text)


def parse_timestamps(display_time, updated_at):
    """For v3."""
    dt = display_time / 1000
    sh = ZoneInfo("Asia/Shanghai")
    tz = sh if datetime.fromtimestamp(dt, sh).hour == 0 else ZoneInfo("UTC")
    return datetime.fromtimestamp(dt, tz), datetime.fromtimestamp(updated_at / 1000, tz)


async def from_live_website():
    async with aiohttp.ClientSession() as session:
        devices = (await get_one(session, "https://reolink.com/wp-json/reo-v2/download/product/selection-list", "json"))["data"]
    urls = [f"https://reolink.com/wp-json/reo-v2/download/firmware/?dlProductId={dev['id']}" for dev in devices]
    firmwares = []
    for response in await get_all(urls, "json"):
        for data in response["data"]:
            for fw in data["firmwares"]:
                hw_ver = fw["hardwareVersion"][0]
                note = fragment_fromstring(fw["note"], create_parent=True).text_content()
                fw["firmware_id"] = fw.pop("id")
                fw["hw_ver_id"] = hw_ver["id"]
                fw["model_id"] = hw_ver["dlProduct"]["id"]
                fw["note"] = sanitize(note)
                fw["url"] = fw["url"].replace("%2F", '/')
                fw["model"] = hw_ver["dlProduct"]["title"]
                fw["hw_ver"] = hw_ver["title"].strip()
                fw["changelog"] = parse_changes(fw.pop("new"))
                fw["display_time"], fw["updated_at"] = parse_timestamps(fw.pop("displayTime"), fw["updated_at"])
                del fw["hardwareVersion"]
                if fw not in firmwares:
                    firmwares.append(fw)
    return devices, firmwares
    # with open("reolink_fw.json", "w", encoding="utf8") as f:
    #     json.dump(merged_dict, f, indent=2, ensure_ascii=False)
    # with open("README.md", "w", encoding="utf8") as f:
    #     f.write(make_readme(merged_dict))


def parse_old_page_firmware(text):
    """For https://reolink.com/firmware pages."""


ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/102.0.0.0 Safari/537.36"
def get_archives_v1_links():
    # print("Getting snapshots")
    cdx = WaybackMachineCDXServerAPI("https://reolink.com/firmware", ua, filters=["statuscode:200"])
    snapshots = (snap.archive_url for snap in cdx.snapshots())  # set later?
    # print("Getting HTML pages")
    links = []
    for response in asyncio.run(get_all(snapshots, "text", 20)):
        doc = document_fromstring(response)
        for a in doc.iter("a"):
            href = a.get("href")
            if href is not None and ".zip" in href:
                link = "http" + href.split("http")[-1]
                if link not in links:  # Keep order, don't use set.
                    links.append(link)
    return links


def parse_old_support_page_changes(text):
    match = re.search("(?:\s*What's new:?)?(.*?)(?:Note:|Before upgrading)", text, re.DOTALL)
    if not match:
        return []
    new = match.group(1).strip()
    by_lf = new.split('\n')
    by_nb = re.split("[0-9]{1,2}\. ", new)[1:]
    # If lengths are equal, take by_nb because it's the one without the numbers.
    return [sanitize(t) for t in (by_lf if len(by_lf) > len(by_nb) else by_nb)]


async def get_and_parse_old_support_page(session, url):
    """For https://support.reolink.com/hc/en-us/articles/ pages."""
    html = await get_one(session, url, "text")
    doc = document_fromstring(html)
    main = doc.find("./body/main")
    firmwares = []
    try:
        title = doc.find("./head/title").text
        # Could also use date in link or in firmware.
        dt = dtparse(title.split("Firmware")[0]).date()
        for body in main.find_class("article-body"):
            if new := parse_old_support_page_changes(body.text_content()):
                break
        for table in main.findall(".//table"):
            for tr in table.iter("tr"):
                if len(tr) == 3:
                    model, firmware, hardware = tr
                elif len(tr) == 4:
                    model, firmware, _, hardware = tr
                elif len(tr) == 2:
                    model, firmware = tr
                    hardware = None
                if "model" in model.text_content().lower():
                    continue  # Ignore table header.
                a = firmware.find(".//a")  # xpath because some pages have the <a> under a span, and also multiple <a>s with different links????
                firmwares.append({
                    "model": sanitize(model.text_content()),
                    "version": sanitize(a.text_content()),
                    "hw_ver": sanitize(hardware.text_content()) if hardware is not None else None,
                    "display_time": dt,
                    "url": "http" + a.get("href").split("http")[-1],
                    "changelog": new
                })
    except Exception as e:
        return url, [{"error": repr(e)}]
    return url, firmwares


def fw_hash(fw):
    return hash(fw["model"] + fw["version"] + str(fw["hw_ver"]) + fw["url"] + ''.join(fw["changelog"]))


async def from_support_archives():
    cdx = WaybackMachineCDXServerAPI("https://support.reolink.com/hc/en-us/articles/*", ua, filters=["statuscode:200", "original:.*[0-9]-Firmware-for.*"])
    urls = set(snap.archive_url for snap in cdx.snapshots())
    conn = aiohttp.TCPConnector(limit_per_host=20)
    async with aiohttp.ClientSession(connector=conn) as session:
        tuples = await asyncio.gather(*[get_and_parse_old_support_page(session, url) for url in urls])
    hdict = {}
    errors = []
    for url, firmwares in tuples:
        if "error" in firmwares[0]:
            errors.append({
                **firmwares[0],
                "archive_url": url
            })
            continue
        for fw in firmwares:
            h = fw_hash(fw)
            if h in hdict:
                hdict[h]["archive_url"].append(url)
            else:
                hdict[h] = {
                    **fw,
                    "archive_url": [url]
                }
    return list(hdict.values()) + errors


async def from_archives_v1():
    from fwinfo import get_info
    urls = get_archives_v1_links()
    return await asyncio.gather(*[get_info(url) for url in urls])


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
