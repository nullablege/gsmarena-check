import time
import re
import os
import json
import requests
import smtplib
from email.mime.text import MIMEText
from datetime import datetime
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager
from selenium.common.exceptions import TimeoutException, NoSuchElementException, ElementClickInterceptedException

# --- GSMArena Kontrol Scripti Konfigürasyonu ---
TARGET_URL = 'https://www.gsmarena.com/'
DATA_FILE_NAME = 'last_phones_data.json'
LIMIT_PHONES = 5 # Kontrol edilecek son telefon sayısı (önceki script'ten)

# --- Selenium Scripti Konfigürasyonu (Secrets'tan alınacak) ---
PHP_SAVE_URL = os.environ.get('PHP_SAVE_URL_ENV', 'https://egeaytac.com.tr/kaydet.php') # Varsayılan, secret ile override edilebilir
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY_ENV') # GitHub Secret'tan

# --- E-posta Ayarları (GitHub Actions Secrets'tan) ---
SMTP_SERVER = os.environ.get('SMTP_SERVER_ENV')
SMTP_PORT_STR = os.environ.get('SMTP_PORT_ENV', '587')
SMTP_USERNAME = os.environ.get('SMTP_USERNAME_ENV')
SMTP_PASSWORD = os.environ.get('SMTP_PASSWORD_ENV')
SENDER_EMAIL = os.environ.get('SENDER_EMAIL_ENV')
RECEIVER_EMAIL = os.environ.get('RECEIVER_EMAIL_ENV')
EMAIL_SUBJECT_PREFIX = '[GSMArena Monitor] '

# --- Ortak Başlıklar ---
REQUESTS_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
}

# === GSMArena Kontrol Fonksiyonları (Önceki script'ten) ===
def get_website_content_requests(url):
    try:
        response = requests.get(url, headers=REQUESTS_HEADERS, timeout=30)
        response.raise_for_status()
        return response.text
    except requests.exceptions.RequestException as e:
        print(f"Hata (Requests): Sayfa içeriği çekilemedi. {e}")
        return None

def parse_latest_phones_from_main_page(html_content, limit=3):
    if not html_content: return []
    soup = BeautifulSoup(html_content, 'html.parser')
    phones = []
    
    latest_devices_module = None
    # GSMArena'nın ana sayfasındaki "Latest devices" bölümünün yapısı değişebilir.
    # Daha sağlam bir selektör bulmak gerekebilir.
    # Örnek: <div class="module module-latest"> <h4 class="section-heading">Latest devices</h4> ...
    
    # Öncelikle doğru başlığı içeren modülü bulmaya çalışalım
    possible_modules = soup.find_all('div', class_='module-latest')
    if not possible_modules:
        possible_modules = soup.find_all('div', class_=lambda x: x and 'module' in x and 'latest' in x) # Daha genel

    for module in possible_modules:
        heading = module.find(['h3', 'h4'], class_='section-heading')
        if heading and ('Latest devices' in heading.get_text() or 'Latest additions' in heading.get_text()):
            latest_devices_module = module
            break
            
    if not latest_devices_module:
        print("Hata: Ana sayfada 'Latest devices' modülü bulunamadı. HTML yapısı değişmiş olabilir.")
        # Yedek olarak tüm module-phones-link'leri deneyebiliriz
        all_links_on_page = soup.find_all('a', class_='module-phones-link')
        if not all_links_on_page:
            print("Kritik Hata: Hiçbir telefon linki bulunamadı.")
            return []
        phone_links = all_links_on_page
        print("Uyarı: 'Latest devices' modülü bulunamadı, sayfadaki tüm telefon linkleri taranıyor (limitli).")
    else:
        phone_links = latest_devices_module.find_all('a', class_='module-phones-link')

    if not phone_links:
        print("Hata: 'Latest devices' modülünde telefon linkleri bulunamadı.")
        return []

    # print(f"Bulunan toplam link sayısı: {len(phone_links)}")
    parsed_count = 0
    for link_tag in phone_links:
        if parsed_count >= limit: break
        
        phone_name_tag = link_tag.find('span') # Genellikle <span> içinde oluyor
        phone_name = None

        if phone_name_tag:
            phone_name = phone_name_tag.get_text(strip=True)
        else: # Eğer span yoksa, <br> sonrası metni deneyelim (eski yapı)
            br_tag = link_tag.find('br')
            if br_tag and br_tag.next_sibling and isinstance(br_tag.next_sibling, str):
                phone_name = br_tag.next_sibling.strip()
            elif link_tag.get_text(strip=True): # En son çare linkin kendi metni
                 phone_name = link_tag.get_text(strip=True)


        href = link_tag.get('href')
        # print(f"DEBUG: Link Tag: {link_tag}, Name: {phone_name}, Href: {href}")

        if phone_name and href:
            # İsim "Opinions", "Review", "Prices" gibi şeylerse atla
            if any(keyword in phone_name for keyword in ["Opinions", "Review", "Prices", "Compare", "Pictures"]):
                continue

            full_link = 'https://www.gsmarena.com/' + href.lstrip('/') if not href.startswith('http') else href
            # Dublikasyon önleme (sadece link bazlı, çünkü isimler bazen farklı formatta gelebiliyor)
            if not any(p['link'] == full_link for p in phones):
                 phones.append({'name': phone_name, 'link': full_link})
                 parsed_count += 1
    
    if not phones:
        print("Uyarı: Hiçbir telefon ayrıştırılamadı. Selektörleri kontrol edin.")
    return phones


def load_data_from_file(file_path):
    if os.path.exists(file_path):
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            print(f"Uyarı: {file_path} okunamadı veya bozuk: {e}. Boş liste ile devam edilecek.")
            return []
    return []

def save_data_to_file(file_path, data_list):
    try:
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(data_list, f, indent=4, ensure_ascii=False)
        print(f"Veri dosyası '{file_path}' güncellendi.")
    except IOError as e:
        print(f"Hata: {file_path} dosyasına yazılamadı. {e}")

def send_email_notification(subject, body):
    if not all([SMTP_SERVER, SMTP_USERNAME, SMTP_PASSWORD, SENDER_EMAIL, RECEIVER_EMAIL]):
        print("E-posta ayarları eksik, bildirim gönderilemedi. Lütfen GitHub Secrets'ı kontrol edin: SMTP_SERVER_ENV, SMTP_PORT_ENV, SMTP_USERNAME_ENV, SMTP_PASSWORD_ENV, SENDER_EMAIL_ENV, RECEIVER_EMAIL_ENV")
        return False
    try:
        smtp_port = int(SMTP_PORT_STR)
        msg = MIMEText(body, 'plain', 'utf-8')
        msg['Subject'] = subject
        msg['From'] = SENDER_EMAIL
        msg['To'] = RECEIVER_EMAIL

        with smtplib.SMTP(SMTP_SERVER, smtp_port) as server:
            if smtp_port == 587:
                 server.starttls()
            server.login(SMTP_USERNAME, SMTP_PASSWORD)
            server.sendmail(SENDER_EMAIL, RECEIVER_EMAIL, msg.as_string())
        print(f"Bildirim e-postası {RECEIVER_EMAIL} adresine gönderildi.")
        return True
    except Exception as e:
        print(f"Hata: E-posta gönderilemedi. {e}")
        return False

def commit_and_push_data_file(file_path, commit_message):
    try:
        os.system(f'git config --global user.name "GitHub Action Bot"')
        os.system(f'git config --global user.email "actions@github.com"')
        os.system(f'git add {file_path}')
        if os.system(f'git diff --staged --quiet') == 0:
            print(f"'{file_path}' dosyasında commit edilecek değişiklik bulunamadı.")
            return True
        
        os.system(f'git commit -m "{commit_message}"')
        # Push işlemi için workflow dosyasında `actions/checkout@v3` veya v4'ün `persist-credentials: true`
        # ve `GITHUB_TOKEN` için doğru izinlerin (contents: write) ayarlandığından emin olun.
        # Ya da Personal Access Token (PAT) kullanılıyorsa, checkout adımında ayarlanmalı.
        if os.system('git push') != 0:
            print(f"Hata: '{file_path}' dosyası GitHub'a push edilemedi. Lütfen workflow izinlerini kontrol edin.")
            return False
        print(f"'{file_path}' dosyası başarıyla GitHub'a push edildi.")
        return True
    except Exception as e:
        print(f"Hata: Veri dosyası commit/push edilemedi. {e}")
        return False

# === Selenium Fonksiyonları (Önceki script'ten uyarlanmış) ===
def setup_driver_options_selenium():
    options = Options()
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option('useAutomationExtension', False)
    # options.add_argument("--start-maximized") # Headless'ta anlamsız
    options.add_argument("--headless") # GitHub Actions için headless mod önemli
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument('user-agent=Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')
    options.add_argument("accept-language=tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7")
    options.page_load_strategy = 'eager'
    prefs = {"credentials_enable_service": False, "profile.password_manager_enabled": False}
    options.add_experimental_option("prefs", prefs)
    return options

def get_element_text_by_strategy_selenium(driver, wait, spec_info, default_value="Bilgi Yok"):
    label = spec_info["label"]
    selector_value = spec_info["value"]
    selector_type = spec_info["type"]
    process_as_html = spec_info.get("process_as_html", False)
    target_attribute = spec_info.get("attribute")
    element = None
    try:
        if selector_type == "data-spec":
            base_selector = spec_info.get("base_selector", "#specs-list td.nfo")
            if spec_info.get("child_a"):
                selector_css_for_parent = f"{base_selector}[data-spec='{selector_value}']"
                parent_element = wait.until(EC.visibility_of_element_located((By.CSS_SELECTOR, selector_css_for_parent)))
                element = parent_element.find_element(By.TAG_NAME, "a")
            else:
                selector_css = f"{base_selector}[data-spec='{selector_value}']"
                element = wait.until(EC.visibility_of_element_located((By.CSS_SELECTOR, selector_css)))
        elif selector_type == "xpath":
            element = wait.until(EC.visibility_of_element_located((By.XPATH, selector_value)))
        elif selector_type == "css":
            element = wait.until(EC.visibility_of_element_located((By.CSS_SELECTOR, selector_value)))

        if element:
            if target_attribute:
                attr_content = element.get_attribute(target_attribute)
                return attr_content.strip() if attr_content and attr_content.strip() else default_value
            elif process_as_html:
                content_html = element.get_attribute('innerHTML').strip()
                content_text_lines = [re.sub(r'<[^>]+>', '', line).strip() for line in content_html.split('<br>') if re.sub(r'<[^>]+>', '', line).strip()]
                clean_text = "\n".join(content_text_lines)
                clean_text = re.sub(r'\s*\n\s*', '\n', clean_text).strip()
                clean_text = re.sub(r' +', ' ', clean_text).strip()
                return clean_text if clean_text else default_value
            else:
                content = driver.execute_script("return arguments[0].innerText || arguments[0].textContent;", element).strip()
                return content if content else default_value
        return default_value
    except (TimeoutException, NoSuchElementException):
        return default_value
    except Exception:
        return default_value

def _handle_popups_selenium(driver, wait_time=3): # GitHub Actions'da popup'lar daha az sorun olmalı, yine de dursun.
    popup_selectors = [
        "//button[contains(translate(., 'ACCEPPTAGREE', 'acepptagree'), 'accept') or contains(translate(., 'ACCEPPTAGREE', 'acepptagree'), 'agree')]",
        "//button[@id='L2AGLb']"
    ]
    for selector in popup_selectors:
        try:
            button = WebDriverWait(driver, wait_time).until(EC.element_to_be_clickable((By.XPATH, selector)))
            driver.execute_script("arguments[0].click();", button)
            time.sleep(0.5) # Pop-up sonrası sayfanın oturması için
            return True
        except: pass
    return False

def check_review_link_element_selenium(driver, wait):
    _handle_popups_selenium(driver)
    try:
        return wait.until(EC.element_to_be_clickable((
            By.XPATH,
            "//ul[contains(@class, 'article-info-meta')]//li[contains(@class, 'article-info-meta-link-review')]//a[normalize-space()='Review' or normalize-space()='İnceleme']"
        )))
    except: return None

def fetch_review_text_from_pages_selenium(driver, wait_critical, wait_general):
    all_review_texts = []
    page_count = 1
    MAX_REVIEW_PAGES = 10 # Actions'da çok uzun sürmemesi için limit düşürülebilir
    try:
        wait_critical.until(EC.visibility_of_element_located((By.ID, "review-body")))
    except TimeoutException:
        return "İnceleme İçeriği Yüklenemedi (review-body ilk yüklemede zaman aşımı)"

    while page_count <= MAX_REVIEW_PAGES:
        _handle_popups_selenium(driver)
        try:
            review_body = wait_general.until(EC.visibility_of_element_located((By.ID, "review-body")))
            paragraphs = review_body.find_elements(By.XPATH, ".//p[string-length(normalize-space(self::*)) > 0]")
            current_page_text = [driver.execute_script("return arguments[0].innerText;", p).strip() for p in paragraphs if driver.execute_script("return arguments[0].innerText;", p).strip()]
            all_review_texts.extend(current_page_text)
        except: pass # Hata olursa atla, sonraki sayfaya geçmeyi dene

        try:
            next_page_link_xpath = "//a[contains(@class, 'pages-next') and not(contains(@class, 'disabled')) and @href and string-length(normalize-space(@href)) > 1]"
            next_page_link = wait_general.until(EC.element_to_be_clickable((By.XPATH, next_page_link_xpath)))
            driver.execute_script("arguments[0].click();", next_page_link)
            page_count += 1
            wait_critical.until(EC.visibility_of_element_located((By.ID, "review-body"))) # Yeni sayfanın yüklenmesini bekle
            time.sleep(0.5) 
        except: break # Sonraki sayfa yoksa veya tıklanamazsa döngüyü bitir
    return "\n\n".join(all_review_texts) if all_review_texts else "İnceleme Metni Bulunamadı"

def summarize_with_gemini_selenium(text_to_summarize, api_key, model_name="gemini-1.5-flash-latest"): # Daha hızlı model
    if not api_key: return f"Gemini API Anahtarı Eksik ({text_to_summarize[:50 if text_to_summarize else 0]}...)"
    if not text_to_summarize or text_to_summarize.startswith("İnceleme Metni Bulunamadı") or text_to_summarize.startswith("İnceleme İçeriği Yüklenemedi"):
        return text_to_summarize
    
    api_url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
    prompt = (
        "Aşağıdaki İngilizce telefon inceleme metnini, bir editörün yazdığı gibi akıcı ve bilgilendirici bir şekilde TÜRKÇE'ye çevir ve özetle. "
        "Anahtar özelliklere (kamera, performans, batarya) odaklan. "
        "Sadece çevrilmiş ve özetlenmiş TÜRKÇE metni ver, başka bir açıklama ekleme.\n\n"
        f"KAYNAK METİN:\n{text_to_summarize}"
    ) # Prompt basitleştirildi.
    payload = {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"temperature": 0.5, "maxOutputTokens": 4096}}
    headers = {'Content-Type': 'application/json'}
    try:
        response = requests.post(api_url, headers=headers, data=json.dumps(payload), timeout=180) # Timeout düşürüldü
        response.raise_for_status()
        response_json = response.json()
        if 'candidates' in response_json and response_json['candidates'] and \
           'content' in response_json['candidates'][0] and 'parts' in response_json['candidates'][0]['content']:
            return response_json['candidates'][0]['content']['parts'][0]['text'].strip()
        return f"Gemini API Yanıt Formatı Hatalı ({model_name})"
    except Exception as e:
        return f"Gemini API Hatası ({model_name}): {str(e)[:100]}"

phone_specs_definitions = [
    {"label": "Model Adı", "type": "data-spec", "value": "modelname", "base_selector": "h1.specs-phone-name-title", "critical": True, "default_value": "Model Adı Yok"},
    {"label": "Resim URL", "type": "css", "value": "div.specs-photo-main > a > img", "attribute": "src", "default_value": "Resim Yok"},
    {"label": "Network Teknolojisi", "type": "xpath", "value": "(//*[@id='specs-list']//th[contains(text(),'Network')]//following-sibling::tr[1]//td[@data-spec='nettech'])[1] | (//*[@id='specs-list']//a[@href='network-bands.php3']/../following-sibling::td[@data-spec='nettech'])[1] | //*[@data-spec='nettech'][1]", "critical": True},
    {"label": "Duyurulma Tarihi", "type": "data-spec", "value": "year"},
    {"label": "Piyasaya Çıkış Durumu", "type": "data-spec", "value": "status"},
    {"label": "Boyutlar", "type": "data-spec", "value": "dimensions"},
    {"label": "Ağırlık", "type": "data-spec", "value": "weight"},
    {"label": "Gövde Malzemesi", "type": "data-spec", "value": "build"},
    {"label": "Sim", "type": "data-spec", "value": "sim", "process_as_html":True},
    {"label": "Gövde Diğer (IP vb.)", "type": "data-spec", "value": "bodyother", "process_as_html":True},
    {"label": "Ekran Tipi", "type": "data-spec", "value": "displaytype"},
    {"label": "Ekran Boyutu", "type": "data-spec", "value": "displaysize"},
    {"label": "Ekran Çözünürlüğü", "type": "data-spec", "value": "displayresolution"},
    {"label": "Ekran Koruması", "type": "data-spec", "value": "displayprotection"},
    {"label": "Ekran Diğer Özellikler", "type": "xpath", "value": "//table[.//th[contains(text(),'Display')]]//td[@data-spec='displayother']","process_as_html":True},
    {"label": "İşletim Sistemi", "type": "data-spec", "value": "os"},
    {"label": "Yonga Seti", "type": "data-spec", "value": "chipset"},
    {"label": "CPU", "type": "data-spec", "value": "cpu"},
    {"label": "GPU", "type": "data-spec", "value": "gpu"},
    {"label": "Hafıza Kartı Yuvası", "type": "data-spec", "value": "memoryslot"},
    {"label": "Dahili Hafıza", "type": "data-spec", "value": "internalmemory", "process_as_html": True},
    {"label": "Ana Kamera Modülleri", "type": "data-spec", "value": "cam1modules", "process_as_html": True},
    {"label": "Ana Kamera Özellikleri", "type": "data-spec", "value": "cam1features"},
    {"label": "Ana Kamera Video", "type": "data-spec", "value": "cam1video", "process_as_html": True},
    {"label": "Ön Kamera Modülleri", "type": "data-spec", "value": "cam2modules", "process_as_html": True},
    {"label": "Ön Kamera Özellikleri", "type": "data-spec", "value": "cam2features"},
    {"label": "Ön Kamera Video", "type": "data-spec", "value": "cam2video", "process_as_html": True},
    {"label": "Hoparlör", "type": "xpath", "value": "//table[.//th[text()='Sound']]//td[@class='ttl']/a[normalize-space(text())='Loudspeaker']/parent::td/following-sibling::td[@class='nfo']"},
    {"label": "3.5mm Jack", "type": "xpath", "value": "//table[.//th[text()='Sound']]//td[@class='ttl']/a[normalize-space(text())='3.5mm jack']/parent::td/following-sibling::td[@class='nfo']"},
    {"label": "WLAN", "type": "data-spec", "value": "wlan"},
    {"label": "Bluetooth", "type": "data-spec", "value": "bluetooth"},
    {"label": "Konumlandırma (GPS)", "type": "data-spec", "value": "gps", "process_as_html": True},
    {"label": "NFC", "type": "data-spec", "value": "nfc"},
    {"label": "Radyo", "type": "data-spec", "value": "radio"},
    {"label": "USB", "type": "data-spec", "value": "usb"},
    {"label": "Sensörler", "type": "data-spec", "value": "sensors", "process_as_html": True},
    {"label": "Batarya Tipi", "type": "data-spec", "value": "batdescription1", "process_as_html": True},
    {"label": "Şarj Özellikleri", "type": "xpath", "value": "//table[.//th[text()='Battery']]//td[@class='ttl']/a[normalize-space(text())='Charging']/parent::td/following-sibling::td[@class='nfo']", "process_as_html": True},
    {"label": "Renkler", "type": "data-spec", "value": "colors"},
    {"label": "Fiyat", "type": "xpath", "value": "(//td[@data-spec='price']/a|//td[@data-spec='price'])[1]"},
    {"label": "Performans Testleri (AnTuTu, GeekBench etc.)", "type": "data-spec", "value": "tbench", "process_as_html": True},
]

def fetch_phone_data_selenium(url, specs_definitions, gemini_api_key_param):
    print(f"\n--- Selenium: {url} İÇİN VERİ ÇEKME BAŞLATILIYOR ---")
    chrome_options = setup_driver_options_selenium()
    driver = None
    initial_specs_data = {spec_def["label"]: "Veri Çekilemedi (Selenium)" for spec_def in specs_definitions}
    initial_review_status = "Bilinmiyor (Selenium)"
    initial_processed_review = "İnceleme Yok (Selenium)"
    initial_raw_review = "İnceleme Yok (Selenium)"

    try:
        print("ChromeDriverManager.install() çağrılıyor...")
        service = Service(ChromeDriverManager().install())
        print("webdriver.Chrome çağrılıyor...")
        driver = webdriver.Chrome(service=service, options=chrome_options)
        print("WebDriver başarıyla başlatıldı.")
    except Exception as e:
        print(f"Hata (Selenium): WebDriverManager veya Chrome başlatma hatası. {e}")
        if driver: driver.quit()
        initial_review_status = "WebDriver Başlatma Hatası (Selenium)"
        return initial_specs_data, initial_review_status, initial_processed_review, initial_raw_review

    try:
        driver.get(url)
        _handle_popups_selenium(driver) # Sayfa yüklendikten sonra pop-up'ları handle et
    except Exception as e:
        print(f"Hata (Selenium): URL yüklenirken hata. {e}")
        if driver: driver.quit()
        initial_review_status = "URL Yükleme Hatası (Selenium)"
        return initial_specs_data, initial_review_status, initial_processed_review, initial_raw_review

    general_wait_time = 10 # Actions'da network yavaş olabilir
    critical_wait_time = 20
    wait_critical = WebDriverWait(driver, critical_wait_time)
    wait_general = WebDriverWait(driver, general_wait_time)

    try:
        WebDriverWait(driver, critical_wait_time).until(
            EC.any_of(
                EC.visibility_of_element_located((By.CSS_SELECTOR, "h1.specs-phone-name-title[data-spec='modelname']")),
                EC.visibility_of_element_located((By.ID, "specs-list"))
            )
        )
    except TimeoutException:
        print(f"Hata (Selenium): Ana sayfa elementleri zamanında yüklenemedi.")
        if driver: driver.quit()
        initial_review_status = "Ana Elementler Yüklenemedi (Selenium)"
        return initial_specs_data, initial_review_status, initial_processed_review, initial_raw_review
    
    specs_data_dict = {}
    for spec_def in specs_definitions:
        default_val = spec_def.get("default_value", "Bilgi Yok")
        text_content = get_element_text_by_strategy_selenium(driver, wait_general, spec_def, default_value=default_val)
        specs_data_dict[spec_def["label"]] = text_content

    review_status_text = "Review Yok"
    raw_review_content = "İnceleme Metni Yok (Selenium)"
    processed_review_content = raw_review_content
    review_link_element = check_review_link_element_selenium(driver, wait_general)

    if review_link_element:
        review_status_text = "Review Var"
        try:
            driver.execute_script("arguments[0].click();", review_link_element) # Direkt tıklama
            time.sleep(2) # Sayfa geçişi için bekleme
            _handle_popups_selenium(driver)
            raw_review_content = fetch_review_text_from_pages_selenium(driver, wait_critical, wait_general)
            if gemini_api_key_param and not (raw_review_content.startswith("İnceleme Metni Bulunamadı") or raw_review_content.startswith("İnceleme İçeriği Yüklenemedi")):
                processed_review_content = summarize_with_gemini_selenium(raw_review_content, gemini_api_key_param)
            else:
                processed_review_content = raw_review_content
        except Exception as e:
            print(f"Hata (Selenium): Review işlenirken hata. {e}")
            raw_review_content = f"İnceleme Metni Yok (Review işleme hatası: {str(e)[:50]})"
            processed_review_content = raw_review_content
            review_status_text = "Review Var (Ama işlenemedi)"
    
    if driver: driver.quit()
    return specs_data_dict, review_status_text, processed_review_content, raw_review_content

def save_data_to_php_selenium(phone_data_dict, php_url_param):
    try:
        headers = {'Content-Type': 'application/json; charset=utf-8'}
        json_payload_utf8 = json.dumps(phone_data_dict, ensure_ascii=False).encode('utf-8')
        response = requests.post(php_url_param, data=json_payload_utf8, headers=headers, timeout=120) # Timeout artırıldı
        response.raise_for_status()
        try: response_json = response.json()
        except json.JSONDecodeError: return False, f"PHP yanıtı JSON formatında değil: {response.text[:200]}"
        
        if response_json.get("status") == "success":
            return True, f"Veritabanı işlemi başarılı (ID: {response_json.get('id')})"
        else:
            return False, f"Veritabanı sunucu mesajı: {response_json.get('message', 'Bilinmeyen PHP yanıtı')}"
    except requests.exceptions.HTTPError as http_err:
        return False, f"PHP HTTP hatası: {http_err} - Yanıt: {http_err.response.text[:200] if http_err.response else ''}"
    except requests.exceptions.RequestException as e:
        return False, f"PHP istek hatası: {e}"

def process_single_phone_with_selenium(phone_url, phone_name_from_main_page, specs_defs, gemini_key, php_url_param):
    """Tek bir telefonu Selenium ile işler ve PHP'ye gönderir."""
    print(f"\nSelenium ile işleniyor: {phone_name_from_main_page} ({phone_url})")
    
    specs_dict, review_status, gemini_review, raw_review = fetch_phone_data_selenium(phone_url, specs_defs, gemini_key)

    model_adi_value = specs_dict.get("Model Adı", phone_name_from_main_page) # Ana sayfadaki ismi yedek olarak kullan
    if model_adi_value == "Veri Çekilemedi (Selenium)" or model_adi_value == "Model Adı Yok" or not model_adi_value:
        model_adi_value = phone_name_from_main_page # Eğer Selenium çekemezse ana sayfadaki ismi kullan

    marka_value = "Marka Yok"
    if model_adi_value and model_adi_value not in ["Bilinmeyen Model", "Marka Yok", "Model Adı Yok"] and not model_adi_value.startswith("Bilinmeyen Model ("):
        parts = model_adi_value.split(' ')
        if parts: marka_value = parts[0]
    
    resim_url_value = specs_dict.get("Resim URL", "Resim Yok")

    data_for_php = {
        "url": phone_url,
        "model_adi": model_adi_value,
        "marka": marka_value,
        "resim_url": resim_url_value,
        "review_status": review_status,
        "processed_review_content": gemini_review,
        "raw_review_content": raw_review,
        "specs": []
    }

    for spec_def in specs_defs:
        label = spec_def["label"]
        if label in ["Model Adı", "Resim URL"]: continue
        value = specs_dict.get(label, spec_def.get("default_value", "Bilgi Yok"))
        data_for_php["specs"].append({"label": label, "value": value})
    
    if review_status in ["WebDriver Başlatma Hatası (Selenium)", "URL Yükleme Hatası (Selenium)", "Ana Elementler Yüklenemedi (Selenium)"]:
        print(f"Kritik Selenium hatası ({phone_url}). PHP'ye gönderilmeyecek.")
        return False, f"Selenium kritik hata: {review_status}"

    php_success, php_message = save_data_to_php_selenium(data_for_php, php_url_param)
    if php_success:
        print(f"Veriler başarıyla veritabanına aktarıldı ({phone_url}). Mesaj: {php_message}")
        return True, f"Siteye eklendi. {php_message}"
    else:
        print(f"VERİLER VERİTABANINA KAYDEDİLEMEDİ ({phone_url}). Mesaj: {php_message}")
        return False, f"Siteye eklenemedi. {php_message}"

# === Ana İş Akışı ===
if __name__ == "__main__":
    print(f"GSMArena monitör ve scrape script'i başlatıldı ({datetime.now().strftime('%Y-%m-%d %H:%M:%S')}).")
    print(f"Veri kaydetme hedefi: {PHP_SAVE_URL}")
    if not GEMINI_API_KEY:
        print("UYARI: GEMINI_API_KEY_ENV secret'ı ayarlanmamış. İncelemeler ham kalacak.")

    data_file_full_path = DATA_FILE_NAME

    html = get_website_content_requests(TARGET_URL)
    if not html:
        email_body_error = "GSMArena ana sayfa içeriği çekilemedi. İşlem sonlandırıldı."
        send_email_notification(EMAIL_SUBJECT_PREFIX + "Kritik Hata!", email_body_error)
        print(email_body_error)
        exit(1)

    latest_phones_from_site = parse_latest_phones_from_main_page(html, LIMIT_PHONES)
    if not latest_phones_from_site:
        email_body_error = "GSMArena ana sayfasından telefonlar parse edilemedi. İşlem sonlandırıldı."
        send_email_notification(EMAIL_SUBJECT_PREFIX + "Parse Hatası!", email_body_error)
        print(email_body_error)
        exit(1)

    print("\nSiteden Alınan Son Telefonlar:")
    for p in latest_phones_from_site: print(f"- {p['name']} ({p['link']})")

    last_known_phones = load_data_from_file(data_file_full_path)
    
    # Karşılaştırma için sadece linkleri kullanmak daha stabil olabilir
    current_links_on_site = {p['link'] for p in latest_phones_from_site}
    last_known_links = {p['link'] for p in last_known_phones}

    if current_links_on_site != last_known_links:
        print("\nDeğişiklik tespit edildi!")
        
        newly_added_phones = [p for p in latest_phones_from_site if p['link'] not in last_known_links]
        
        email_subject = ""
        email_body = f"GSMArena'da değişiklikler tespit edildi ({datetime.now().strftime('%Y-%m-%d %H:%M:%S')}):\n"
        
        if newly_added_phones:
            email_subject = EMAIL_SUBJECT_PREFIX + f"{len(newly_added_phones)} Yeni Telefon Tespit Edildi!"
            email_body += "\nYeni Eklenen Telefon(lar):\n"
            
            processed_phone_results = []
            for new_phone in newly_added_phones:
                phone_url_to_process = new_phone['link']
                phone_name_to_process = new_phone['name']
                
                # Selenium ile bu yeni telefonu işle
                # `phone_specs_definitions` bu scope'ta tanımlı olmalı
                success_selenium, message_selenium = process_single_phone_with_selenium(
                    phone_url_to_process,
                    phone_name_to_process,
                    phone_specs_definitions, # Yukarıda tanımlı global değişken
                    GEMINI_API_KEY,
                    PHP_SAVE_URL
                )
                status_message = f"Başarılı: {success_selenium} - Mesaj: {message_selenium}"
                email_body += f"- {phone_name_to_process} ({phone_url_to_process})\n  İşlem Durumu: {status_message}\n"
                processed_phone_results.append({"name": phone_name_to_process, "status": status_message})
            
            # Eğer sadece sıralama değiştiyse ama yeni telefon yoksa
            if not processed_phone_results:
                 email_body += "\nSadece sıralama değişmiş olabilir, yeni telefon tespit edilmedi.\n"
                 email_subject = EMAIL_SUBJECT_PREFIX + "Telefon Listesi Güncellendi (Sıralama Değişikliği)"


        else: # Yeni eklenen yok ama liste farklı (örneğin biri çıktı, sıralama değişti)
            email_subject = EMAIL_SUBJECT_PREFIX + "Telefon Listesi Güncellendi (Yeni Yok)"
            email_body += "\nListede değişiklik var ancak yeni eklenen telefon tespit edilmedi (örn. eski bir telefon listeden çıkmış veya sıralama değişmiş olabilir).\n"

        email_body += "\nSitedeki Mevcut İlk Telefonlar:\n"
        for i, phone in enumerate(latest_phones_from_site):
            email_body += f"{i+1}. {phone['name']} ({phone['link']})\n"

        send_email_notification(email_subject if email_subject else EMAIL_SUBJECT_PREFIX + "Telefon Listesi Güncellendi", email_body)
        
        save_data_to_file(data_file_full_path, latest_phones_from_site) # Her zaman en son çekilen listeyi kaydet
        commit_message = f"GSMArena: Telefon listesi güncellendi ({datetime.now().strftime('%Y-%m-%d')})"
        commit_and_push_data_file(data_file_full_path, commit_message)
    else:
        print("\nTelefon listesi aynı, değişiklik yok.")
        # Değişiklik olmadığında e-posta göndermemek için bu kısmı yorum satırı yapabilirsiniz.
        # send_email_notification(EMAIL_SUBJECT_PREFIX + "Kontrol Tamamlandı (Değişiklik Yok)", "GSMArena telefon listesi kontrol edildi, değişiklik bulunmadı.")

    print("\nİşlem tamamlandı.")
