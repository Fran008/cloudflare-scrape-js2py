import logging
import random
import re

from copy import deepcopy
from time import sleep
from collections import OrderedDict

import js2py
from requests.sessions import Session

try:
    from urlparse import urlparse
    from urlparse import urlunparse
except ImportError:
    from urllib.parse import urlparse
    from urllib.parse import urlunparse

__version__ = "1.9.5"

# Orignally written by https://github.com/Anorov/cloudflare-scrape
# Rewritten by VeNoMouS - <venom@gen-x.co.nz> for https://github.com/VeNoMouS/cloudflare-scrape-js2py - 24/3/2018 NZDT

DEFAULT_USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_13_2) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/65.0.3325.181 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Ubuntu Chromium/65.0.3325.181 Chrome/65.0.3325.181 Safari/537.36",
    "Mozilla/5.0 (Linux; Android 7.0; Moto G (5) Build/NPPS25.137-93-8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/64.0.3282.137 Mobile Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 7_0_4 like Mac OS X) AppleWebKit/537.51.1 (KHTML, like Gecko) Version/7.0 Mobile/11B554a Safari/9537.53",
    "Mozilla/5.0 (Windows NT 6.1; Win64; x64; rv:60.0) Gecko/20100101 Firefox/60.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.13; rv:59.0) Gecko/20100101 Firefox/59.0",
    "Mozilla/5.0 (Windows NT 6.3; Win64; x64; rv:57.0) Gecko/20100101 Firefox/57.0",
]

DEFAULT_USER_AGENT = random.choice(DEFAULT_USER_AGENTS)

BUG_REPORT = """\
Cloudflare may have changed their technique, or there may be a bug in the script.
"""

ANSWER_ACCEPT_ERROR = """\
The challenge answer was not properly accepted by Cloudflare. This can occur if \
the target website is under heavy load, or if Cloudflare is experiencing issues. You can
potentially resolve this by increasing the challenge answer delay (default: 8 seconds). \
For example: cfscrape.create_scraper(delay=15)
"""

class CloudflareScraper(Session):
    def __init__(self, *args, **kwargs):
        self.delay = kwargs.pop("delay", 8)
        super(CloudflareScraper, self).__init__(*args, **kwargs)

        if "requests" in self.headers["User-Agent"]:
            # Set a random User-Agent if no custom User-Agent has been set
            self.headers["User-Agent"] = DEFAULT_USER_AGENT

    def is_cloudflare_challenge(self, resp):
        return (
            resp.status_code == 503
            and resp.headers.get("Server", "").startswith("cloudflare")
            and b"jschl_vc" in resp.content
            and b"jschl_answer" in resp.content
        )

    def request(self, method, url, *args, **kwargs):
        self.headers['Accept-Encoding'] = 'gzip, deflate'
        self.headers['Accept-Language'] = 'en-US,en;q=0.9'
        self.headers['DNT'] = '1'
        
        resp = super(CloudflareScraper, self).request(method, url, *args, **kwargs)
        
        # Check if Cloudflare anti-bot is on
        if self.is_cloudflare_challenge(resp):
            resp = self.solve_cf_challenge(resp, **kwargs)

        return resp

    def solve_cf_challenge(self, resp, **original_kwargs):
        body = resp.text

        self.delay = float(re.search(r"submit\(\);\r?\n\s*},\s*([0-9]+)", body).group(1)) / float(1000)
        sleep(self.delay)  # Cloudflare requires a delay before solving the challenge

        parsed_url = urlparse(resp.url)
        domain = parsed_url.netloc
        submit_url = "{}://{}/cdn-cgi/l/chk_jschl".format(parsed_url.scheme, domain)

        cloudflare_kwargs = deepcopy(original_kwargs)
        headers = cloudflare_kwargs.setdefault('headers', {'Referer': resp.url})
        
        try:
            params = cloudflare_kwargs.setdefault(
                "params", OrderedDict(
                    [
                        ('s', re.search(r'name="s"\svalue="(?P<s_value>[^"]+)', body).group('s_value')),
                        ('jschl_vc', re.search(r'name="jschl_vc" value="(\w+)"', body).group(1)),
                        ('pass', re.search(r'name="pass" value="(.+?)"', body).group(1)),
                    ]
                )
            )

        except Exception as e:
            # Something is wrong with the page.
            # This may indicate Cloudflare has changed their anti-bot
            # technique. If you see this and are running the latest version,
            # please open a GitHub issue so I can update the code accordingly.
            raise ValueError("Unable to parse Cloudflare anti-bots page: %s %s" % (e.message, BUG_REPORT))

        # Solve the Javascript challenge
        params["jschl_answer"] = self.solve_challenge(body, domain)

        # Requests transforms any request into a GET after a redirect,
        # so the redirect has to be handled manually here to allow for
        # performing other types of requests even as the first request.
        method = resp.request.method
        
        cloudflare_kwargs["allow_redirects"] = False
        
        redirect = self.request(method, submit_url, **cloudflare_kwargs)
        redirect_location = urlparse(redirect.headers["Location"])

        if not redirect_location.netloc:
            redirect_url = urlunparse(
                (
                    parsed_url.scheme,
                    domain,
                    redirect_location.path,
                    redirect_location.params,
                    redirect_location.query,
                    redirect_location.fragment
                )
            )
            return self.request(method, redirect_url, **original_kwargs)
        
        return self.request(method, redirect.headers["Location"], **original_kwargs)

    def solve_challenge(self, body, domain):
        try:
            body = re.sub(
                r'function\(p\){var p = eval\(eval\(atob\(".*?"\)\+\(undefined\+""\)\[1\]\+\(true\+""\)\[0\]\+\(\+\(\+!'
                '\+\[\]\+\[\+!\+\[\]\]\+\(!!\[\]\+\[\]\)\[!\+\[\]\+!\+\[\]\+!\+\[\]\]\+\[!\+\[\]\+!\+\[\]\]\+\[\+\[\]\]'
                '\)\+\[\]\)\[\+!\+\[\]\]\+\(false\+\[0\]\+String\)\[20\]\+\(true\+""\)\[3\]\+\(true\+""\)\[0\]\+"Element"'
                '\+\(\+\[\]\+Boolean\)\[10\]\+\(NaN\+\[Infinity\]\)\[10\]\+"Id\("\+\(\+\(20\)\)\["to"\+String\["name"\]\]'
                '\(21\)\+"\)."\+atob\(".*?"\)\)\); return \+\(p\)}\(\);',
                '{};'.format(
                    re.search('<div style="display:none;visibility:hidden;" id="(.*?)">(.*?)<\/div>',
                    body,
                    re.MULTILINE | re.DOTALL).group(2)
                ),
                body
            )
            js = re.search(r"setTimeout\(function\(\){\s+(var "
                        "s,t,o,p,b,r,e,a,k,i,n,g,f.+?\r?\n[\s\S]+?a\.value =.+?)\r?\n", body).group(1)
        except Exception:
            raise ValueError("Unable to identify Cloudflare IUAM Javascript on website. %s" % BUG_REPORT)

        js = re.sub(r"a\.value = ((.+).toFixed\(10\))?", r"\1", js)
        js = re.sub(r"\s{3,}[a-z](?: = |\.).+", "", js).replace("t.length", str(len(domain)))

        js = js.replace('; 121', '')

        js = js.replace(
            'function(p){return eval((true+"")[0]+"."+([]["fill"]+"")[3]+(+(101))["to"+String["name"]](21)[1]+(false+"")'
            '[1]+(true+"")[1]+Function("return escape")()(("")["italics"]())[2]+(true+[]["fill"])[10]+(undefined+"")[2]+'
            '(true+"")[3]+(+[]+Array)[10]+(true+"")[0]+"("+p+")")}',
            't.charCodeAt'
        )

        # Strip characters that could be used to exit the string context
        # These characters are not currently used in Cloudflare's arithmetic snippet
        js = re.sub(r"[\n\\']", "", js)

        if "toFixed" not in js:
            raise ValueError("Error parsing Cloudflare IUAM Javascript challenge. %s" % BUG_REPORT)

        try:
            js = 'a = {{}}; t = "{}";{}'.format(domain, js)
            result = js2py.eval_js(js)
        except Exception:
            logging.error("Error executing Cloudflare IUAM Javascript. %s" % BUG_REPORT)
            raise

        try:
            float(result)
        except Exception:
            raise ValueError("Cloudflare IUAM challenge returned unexpected answer. %s" % BUG_REPORT)

        return result

    @classmethod
    def create_scraper(cls, sess=None, **kwargs):
        """
        Convenience function for creating a ready-to-go CloudflareScraper object.
        """
        scraper = cls(**kwargs)

        if sess:
            attrs = ["auth", "cert", "cookies", "headers", "hooks", "params", "proxies", "data"]
            for attr in attrs:
                val = getattr(sess, attr, None)
                if val:
                    setattr(scraper, attr, val)

        return scraper


    ## Functions for integrating cloudflare-scrape with other applications and scripts

    @classmethod
    def get_tokens(cls, url, user_agent=None, **kwargs):
        scraper = cls.create_scraper()
        if user_agent:
            scraper.headers["User-Agent"] = user_agent

        try:
            resp = scraper.get(url, **kwargs)
            resp.raise_for_status()
        except Exception as e:
            logging.error("'%s' returned an error. Could not collect tokens." % url)
            raise

        domain = urlparse(resp.url).netloc
        cookie_domain = None

        for d in scraper.cookies.list_domains():
            if d.startswith(".") and d in ("." + domain):
                cookie_domain = d
                break
        else:
            raise ValueError("Unable to find Cloudflare cookies. Does the site actually have Cloudflare IUAM (\"I'm Under Attack Mode\") enabled?")

        return (
            {
                "__cfduid": scraper.cookies.get("__cfduid", "", domain=cookie_domain),
                "cf_clearance": scraper.cookies.get("cf_clearance", "", domain=cookie_domain)
            },
            scraper.headers["User-Agent"]
        )

    @classmethod
    def get_cookie_string(cls, url, user_agent=None, **kwargs):
        """
        Convenience function for building a Cookie HTTP header value.
        """
        tokens, user_agent = cls.get_tokens(url, user_agent=user_agent, **kwargs)
        return "; ".join("=".join(pair) for pair in tokens.items()), user_agent

create_scraper = CloudflareScraper.create_scraper
get_tokens = CloudflareScraper.get_tokens
get_cookie_string = CloudflareScraper.get_cookie_string
