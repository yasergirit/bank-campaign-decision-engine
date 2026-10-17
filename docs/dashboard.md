# Aşama 7 — Streamlit karar ekranı

Proje kökü: `<project-root>`.

## Kurulum ve çalıştırma

PyCharm terminalinde proje kökünden:

```bash
uv sync --locked --inexact
uv pip install --python .venv/bin/python --require-hashes -r requirements-ui.lock
```

UI kilidi, API kilidindeki sürümleri ve dondurulmuş pandas/numpy sürümlerini korur.
`uv.lock` ve `requirements-api.lock` değişmedi. Sonraki uv komutlarında
`--inexact` ek paketleri korur. Streamlit 1.66.0 kilitlidir.

İki ayrı terminal veya iki PyCharm Run Configuration kullanın:

```bash
# Terminal 1: karar API'si
.venv/bin/python -m uvicorn bank_campaign.api:app --host 127.0.0.1 --port 8000
```

```bash
# Terminal 2: karar ekranı
.venv/bin/python -m streamlit run app/streamlit_app.py
```

Adres: [Kampanya çalışma alanı](http://127.0.0.1:8502).
8501 mevcut başka bir uygulamada kullanıldığı için proje ayarı 8502'dir.

PyCharm → Run → Edit Configurations → + → Python:

| Ayar | API | Ekran |
|---|---|---|
| Module name | `uvicorn` | `streamlit` |
| Parameters | `bank_campaign.api:app --host 127.0.0.1 --port 8000` | `run app/streamlit_app.py` |
| Interpreter | Projenin `.venv/bin/python` dosyası | Aynı yorumlayıcı |
| Working directory | Proje kökü | Proje kökü |

Servisler zaten çalışıyorsa ikinci kopya başlatmayın. PyCharm Stop veya terminalde
Ctrl+C ile kapatabilirsiniz. Alternatif ekran portu için `--server.port 8503`
ekleyin. API adresi gerekirse Run Configuration ortam değişkeni
`BANK_CAMPAIGN_API_URL` ile belirlenir; varsayılan `http://127.0.0.1:8000`.

## İlk deneme

1. **Örnek adaylar** seçili kalsın. Üç kayıt sentetiktir.
2. K=2, C=3, V=100 ve **Kapasiteye kadar seç** ile **Kampanya listesini oluştur**.
3. Sıra `row_id=2`, `row_id=1`; beklenen abonelik yaklaşık 0,1100; varsayımsal
   toplam maliyet 6; beklenen net değer yaklaşık 5,00.
4. **Seçilen listeyi CSV indir**. Dosya API sırasını, model sürümünü ve tam
   sayısal değerleri korur; ekrandaki yuvarlama CSV'ye uygulanmaz.
5. K=1 yapın. Eski sonuç temizlenir; yeniden seçimde tek kayıt gelir.
6. **Yalnız pozitif beklenen değer** ve C=8 kullanın. Örnek adayların hiçbiri
   koşulu sağlamadığından liste boş olur. Bu bir hata değildir.

UI model yüklemez ve seçim hesabını tekrar uygulamaz; HTTP üzerinden
`POST /campaign/select` çağırır. Sidebar'daki kontrol, API ve model durumunu gösterir.
API kapalıysa yeniden başlatma yönlendirmesi içeren hata görünür.

## CSV yükleme

Önce **Örnek CSV şablonunu indir**, sonra **CSV yükle** seçeneğini kullanın.
Projedeki aynı örnek `examples/campaign_candidates.csv` dosyasındadır.

- UTF-8 veya UTF-8 BOM, virgül ayırıcı; en fazla **2 MiB / 10.000 kayıt**.
- Tam sütun kümesi: `row_id,age,job,marital,education,default,housing,loan,previous,poutcome`.
- Sütun sırası değişebilir; eksik, fazladan ve yinelenen başlık reddedilir.
- `duration`, `y` ve diğer ham kaynak alanları eklenemez.
- `row_id`, `age`, `previous` ASCII rakamlı tam sayı olmalı; ondalık ve boşluklu
  sayılar reddedilir. Kimlikler dosya içinde benzersiz olmalı.
- Resmi kategori yazımlarını kullanın; `unknown` geçerli olduğu alanlarda korunur.
  Kategori listeleri `src/bank_campaign/data/schema.py` içinde.
- Başlık dışında satır olmayan dosya kabul edilir fakat seçim düğmesi kapalıdır.
- Kaynak/dosya/parametre değiştiğinde eski sonuç ve indirme düğmesi temizlenir.

## Sonuçları yorumlama

Beklenen abonelik `sum(p)`, varsayımsal maliyet `adet × C`, varsayımsal beklenen
net değer `sum(p × V − C)`. C ve V aynı para biriminde olmalıdır.
Olasılıklar kalibre edilmemiştir. Bu çıktılar gerçekleşmiş abonelik, nedensel
arama etkisi veya kazanılmış kâr değildir. Etiketsiz adaylar için precision/lift
hesaplanmaz veya gösterilmez.

**Tarihsel değerlendirme** ayrı bir sayfadır. Kayıtlı doğrulama ve dondurulmuş test
raporlarını okur; API kapalıyken de açılır. Mevcut deneyde test K=500 için model
231 tarihsel abonelik, rastgele ortalama 154,065, sabit geçmiş kuralı 372 verdi.
Ekrandaki yeni senaryo ayarları bu sabit raporu değiştirmez. Eğitim, tuning veya
yeni holdout skorlama çalıştırılmaz.

## Doğrulama

```bash
BANK_CAMPAIGN_DB_TESTS=1 uv run --locked --inexact pytest -q
uv run --locked --inexact ruff check src app tests
uv run --locked --inexact ruff format --check src app tests
uv run --locked --inexact mypy src app
```

CSV ve HTTP yardımcı testleri `tests/test_dashboard.py`; Streamlit AppTest ile
UI→istemci→API→politika akışı `tests/e2e/test_dashboard_flow.py` içindedir.
Akış testleri gerçek model dosyası veya ham veri gerektirmeyen sentetik estimator
ile çalışır. Ayrıca çalışan gerçek model/API üzerinden tarayıcıdan indirilen
CSV'nin satır sırası ve tüm değerleri API yanıtıyla birebir doğrulanmıştır.
Sonuç kaydı: `reports/dashboard_verification.json`.

Resmî kaynaklar: [Streamlit AppTest](https://docs.streamlit.io/develop/api-reference/app-testing/st.testing.v1.apptest),
[dosya yükleme](https://docs.streamlit.io/develop/api-reference/widgets/st.file_uploader),
[Session State](https://docs.streamlit.io/develop/api-reference/caching-and-state/st.session_state).
