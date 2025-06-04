import requests
from bs4 import BeautifulSoup
import json
import os
import smtplib
from email.mime.text import MIMEText
from datetime import datetime

# --- Konfigürasyon (Ortam Değişkenlerinden veya doğrudan) ---
TARGET_URL = 'https://www.gsmarena.com/'
DATA_FILE_NAME = 'last_phones_data.json'  # Sadece dosya adı, script ile aynı dizinde olacak
LIMIT_PHONES = 3

# E-posta Ayarları (GitHub Actions Secrets'tan gelecek)
SMTP_SERVER = os.environ.get('SMTP_SERVER_ENV')
SMTP_PORT_STR = os.environ.get('SMTP_PORT_ENV', '587') # Varsayılan port 587
SMTP_USERNAME = os.environ.get('SMTP_USERNAME_ENV')
SMTP_PASSWORD = os.environ.get('SMTP_PASSWORD_ENV')
SENDER_EMAIL = os.environ.get('SENDER_EMAIL_ENV')
RECEIVER_EMAIL = os.environ.get('RECEIVER_EMAIL_ENV')
EMAIL_SUBJECT_NEW_PHONE = 'GSMArena Telefon Listesi Güncellendi! (Python)'

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
}

def get_website_content(url):
    try:
        response = requests.get(url, headers=HEADERS, timeout=20)
        response.raise_for_status()
        return response.text
    except requests.exceptions.RequestException as e:
        print(f"Hata: Sayfa içeriği çekilemedi. {e}")
        return None

def parse_latest_phones(html_content, limit=3):
    if not html_content: return []
    soup = BeautifulSoup(html_content, 'html.parser')
    phones = []
    
    latest_devices_module = None
    all_modules = soup.find_all('div', class_='module-latest')
    for module in all_modules:
        heading = module.find('h4', class_='section-heading')
        if heading and 'Latest devices' in heading.get_text():
            latest_devices_module = module
            break
            
    if not latest_devices_module:
        print("Hata: 'Latest devices' modülü bulunamadı.")
        return []

    phone_links = latest_devices_module.find_all('a', class_='module-phones-link')

    for link_tag in phone_links:
        if len(phones) >= limit: break
        br_tag = link_tag.find('br')
        phone_name = None
        if br_tag and br_tag.next_sibling and isinstance(br_tag.next_sibling, str):
            phone_name = br_tag.next_sibling.strip()
        
        href = link_tag.get('href')
        if phone_name and href:
            full_link = 'https://www.gsmarena.com/' + href.lstrip('/') if not href.startswith('http') else href
            if not any(p['name'] == phone_name for p in phones): # Dublikasyon önleme
                 phones.append({'name': phone_name, 'link': full_link})
    return phones

def load_last_phones(file_path):
    if os.path.exists(file_path):
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            print(f"Uyarı: {file_path} okunamadı veya bozuk: {e}. Boş liste ile devam edilecek.")
            return []
    return []

def save_last_phones(file_path, phones_list):
    try:
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(phones_list, f, indent=4, ensure_ascii=False)
        print(f"Veri dosyası '{file_path}' güncellendi.")
    except IOError as e:
        print(f"Hata: {file_path} dosyasına yazılamadı. {e}")

def send_email_notification(subject, body):
    if not all([SMTP_SERVER, SMTP_USERNAME, SMTP_PASSWORD, SENDER_EMAIL, RECEIVER_EMAIL]):
        print("E-posta ayarları eksik, bildirim gönderilemedi. Lütfen GitHub Secrets'ı kontrol edin.")
        return False
    try:
        smtp_port = int(SMTP_PORT_STR)
        msg = MIMEText(body, 'plain', 'utf-8')
        msg['Subject'] = subject
        msg['From'] = SENDER_EMAIL
        msg['To'] = RECEIVER_EMAIL

        with smtplib.SMTP(SMTP_SERVER, smtp_port) as server:
            if smtp_port == 587: # Genellikle TLS
                 server.starttls()
            server.login(SMTP_USERNAME, SMTP_PASSWORD)
            server.sendmail(SENDER_EMAIL, RECEIVER_EMAIL, msg.as_string())
        print(f"Bildirim e-postası {RECEIVER_EMAIL} adresine gönderildi.")
        return True
    except Exception as e:
        print(f"Hata: E-posta gönderilemedi. {e}")
        return False

def commit_and_push_data_file(file_path, commit_message):
    """Veri dosyasını GitHub repository'sine commit eder ve push eder."""
    try:
        # GitHub Actions'ın çalıştığı dizinde olacağımız varsayılır.
        # git add, commit, push komutları için GITHUB_TOKEN genellikle yeterlidir
        # Eğer push için özel izin gerekiyorsa, workflow'da PAT kullanmak gerekebilir.
        os.system(f'git config --global user.name "GitHub Action Bot"')
        os.system(f'git config --global user.email "actions@github.com"') # GitHub Actions'ın varsayılan e-postası
        os.system(f'git add {file_path}')
        # Sadece değişiklik varsa commit at (gereksiz commit'leri önler)
        if os.system(f'git diff --staged --quiet') == 0: # 0 demek değişiklik yok demek
            print(f"'{file_path}' dosyasında commit edilecek değişiklik bulunamadı.")
            return True # Değişiklik yoksa başarılı kabul et
        
        os.system(f'git commit -m "{commit_message}"')
        os.system('git push')
        print(f"'{file_path}' dosyası başarıyla GitHub'a push edildi.")
        return True
    except Exception as e:
        print(f"Hata: Veri dosyası commit/push edilemedi. {e}")
        return False

# --- Ana İş Akışı ---
if __name__ == "__main__":
    print(f"GSMArena kontrol ediliyor ({datetime.now().strftime('%Y-%m-%d %H:%M:%S')})...")

    data_file_full_path = DATA_FILE_NAME # Script ile aynı dizinde olacak

    html = get_website_content(TARGET_URL)
    if not html:
        print("İşlem sonlandırıldı (HTML alınamadı).")
        exit(1) # Hata kodu ile çık

    latest_phones_from_site = parse_latest_phones(html, LIMIT_PHONES)
    if not latest_phones_from_site:
        print("İşlem sonlandırıldı (Telefon parse edilemedi).")
        exit(1) # Hata kodu ile çık

    print("\nSiteden Alınan Son Telefonlar:")
    for p in latest_phones_from_site: print(f"- {p['name']}")

    last_known_phones = load_last_phones(data_file_full_path)

    # JSON stringlerini karşılaştırarak hem içerik hem sıra değişikliğini kontrol et
    if json.dumps(latest_phones_from_site, sort_keys=True) != json.dumps(last_known_phones, sort_keys=True):
        print("\nSon telefon listesi değişti!")

        notification_body = "GSMArena'daki son telefon listesi güncellendi:\n\n"
        for i, phone in enumerate(latest_phones_from_site):
            notification_body += f"{i+1}. {phone['name']}\n   Link: {phone['link']}\n"
        
        # Hangi telefonların spesifik olarak yeni eklendiğini bul (opsiyonel)
        last_known_links = {p['link'] for p in last_known_phones}
        newly_added_to_list = [p for p in latest_phones_from_site if p['link'] not in last_known_links]
        if newly_added_to_list:
            notification_body += "\nBu listede yeni olanlar:\n"
            for phone in newly_added_to_list:
                notification_body += f"- {phone['name']} ({phone['link']})\n"

        send_email_notification(EMAIL_SUBJECT_NEW_PHONE, notification_body)
        save_last_phones(data_file_full_path, latest_phones_from_site)
        
        # Değişiklikleri repository'ye commit et
        commit_message = f"GSMArena: Son telefon listesi güncellendi ({datetime.now().strftime('%Y-%m-%d')})"
        commit_and_push_data_file(data_file_full_path, commit_message)
    else:
        print("\nTelefon listesi aynı, değişiklik yok.")

    print("\nKontrol tamamlandı.")
