# Aşama 8 — Docker ve sürekli entegrasyon teslim katmanı

Bu aşama API ve Streamlit ekranını aynı, yeniden üretilebilir uygulama imajında
çalıştırır. Eğitim, model seçimi ve test değerlendirmesi konteyner başlangıcının
parçası değildir. PostgreSQL de çevrimiçi skorlamanın bağımlılığı değildir.

## Yerel Docker kullanımı

Önkoşullar Docker Desktop, proje kökündeki `.env` dosyası ve aşağıdaki güvenilir
yerel model dosyasıdır:

```text
artifacts/comparison-v1/selected_model.pkl
SHA-256: 5db3a7f63ce1ae99f5042f5c81c44c3cd9c314fc2c0471d120431e46c9f71354
```

Model Git tarafından izlenmez ve Docker imajına kopyalanmaz. Güvenilir proje sürümündeki dosyayı indirmek için önce `make install`, ardından `make model` çalıştırılır. İndirilen baytlar reports/selection_freeze.json içindeki SHA-256 ile doğrulanır; eşleşmeyen dosya kurulmaz. `make serve` önce
yerel dosyanın dondurulmuş seçim kaydındaki SHA-256 ile birebir eşleştiğini
doğrular; sonra API ve UI servislerini başlatır:

```bash
make serve
make serving-verify
```

Adresler:

- Streamlit: <http://127.0.0.1:18502>
- API Swagger: <http://127.0.0.1:18000/docs>
- API sağlık kontrolü: <http://127.0.0.1:18000/health>

18000 ve 18502 portları, PyCharm ile çalışan 8000 ve 8502 servisleriyle
çakışmaması için seçildi. Gerekirse tek komutta değiştirilebilir:

```bash
API_PORT=28000 UI_PORT=28502 make serve
```

UI sunucusunun API çağrıları Docker ağındaki `http://api:8000` adresine gider.
Tarayıcıda açılan Swagger bağlantısı ise ayrı
`BANK_CAMPAIGN_PUBLIC_API_URL=http://127.0.0.1:18000` ayarını kullanır; `API_PORT`
değiştirildiğinde Compose bu adresi de aynı değere taşır.

Logları izlemek veya yalnız sunum servislerini durdurmak için:

```bash
make serving-logs
make serving-down
```

`serving-down` PostgreSQL konteynerine veya `postgres_data` volume'una dokunmaz.
Compose'taki `serving` profili API/UI'ı olağan veritabanı akışından ayırır.

## İmaj ve çalışma zamanı sınırları

`.dockerignore` aşağıdakileri build context dışında tutar:

- `.env` ve diğer yerel ayarlar,
- model artifact'ları,
- ham ve işlenmiş veri,
- sabit değerlendirme raporları,
- yerel sanal ortam ve geliştirme önbellekleri.

İmaj yalnız uygulama kodu, sentetik örnekler ve hash-kilitli bağımlılıkları
içerir. Python ve `uv` taban imajları digest ile sabitlenmiştir. Linux'ta
LightGBM'in gerektirdiği `libgomp1` çalışma zamanı kurulur. Süreç ayrıcalıksız
`appuser` kullanıcısıyla ve salt okunur dosya sistemiyle çalışır; `/tmp` geçici
`tmpfs` alanıdır. Compose model için `artifacts`, API provenance'i ve UI tarihsel
görünümü için `reports` dizinlerini salt okunur bağlar.

`requirements-ui.lock` macOS'ta üretildiği için Streamlit'in yalnız Darwin dışı
sistemlerde bildirdiği `watchdog` bağımlılığını içermez. Mevcut kilit değiştirilmez;
`requirements-container.lock` bu Linux çalışma zamanı ekini sürüm ve hash ile
sabitler. İmaj ve CI iki kilidi birlikte kurup `uv pip check` ile kurulu paketlerin
metadata sözleşmesini doğrular.

API başlangıcında model byte'ları tekrar dondurulmuş SHA, özellik sırası, paket
sürümleri ve ikili sınıf sözleşmesiyle doğrulanır. Eksik veya farklı artifact
durumunda konteyner çalışsa bile API hazır sayılmaz ve healthcheck başarısız olur.
Bu akış restoration fallback kullanmaz ve modeli yeniden eğitmez.

Temiz imajın yerel artifact olmadan üretilebildiğini doğrulamak için:

```bash
make image
```

Temiz Git klonunda `artifacts/` bulunmaz. `make model`, bu projenin
[model-v1 sürüm dosyasını](https://github.com/yasergirit/bank-campaign-decision-engine/releases/tag/model-v1)
indirip dondurulmuş SHA ile doğrular; mevcut farklı modelin üzerine yazmaz.
Model dosyası kurulmadan `make serve` ön kontrolü başarısız olur.
İndirici pickle açmaz ve eğitim yapmaz. Ham UCI veri seti sunum servisleri için gerekli değildir.

## Canlı doğrulama betiği

`scripts/verify_serving.py` sadece `examples/score_request.json` içindeki üç
sentetik, etiketsiz kaydı kullanır. Betik:

1. yerel artifact SHA'sını doğrular,
2. API ve UI healthcheck'lerini bekler,
3. aynı skoru iki kez alıp deterministik sonucu kontrol eder,
4. kapasite 2 ve kapasite 0 davranışlarını kontrol eder,
5. yüksek maliyetli `positive_value` senaryosunda boş seçim bekler,
6. CSV dışa aktarımının API sıra ve kimliklerini koruduğunu doğrular.

Holdout kayıtları tekrar skorlanmaz; eğitim, tuning veya test metriği üretimi
yapılmaz. Farklı portlar için:

```bash
.venv/bin/python scripts/verify_serving.py \
  --api-url http://127.0.0.1:28000 \
  --ui-url http://127.0.0.1:28502
```

## GitHub Actions CI

`.github/workflows/ci.yml`, `main` push'larında ve pull request'lerde şunları
çalıştırır:

1. Python 3.12 ve sabit `uv` sürümünü kurar,
2. `uv.lock` ile proje, `requirements-ui.lock` ile API/UI bağımlılıklarını kurar,
3. Ruff lint/format ve mypy denetimlerini çalıştırır,
4. PostgreSQL gerektirmeyen testleri çalıştırır,
5. Compose dosyasını doğrular ve artifact içermeyen Docker imajını üretir,
6. PostgreSQL'i başlatıp yalnız izole integration testlerini çalıştırır,
7. başarısızlıkta DB logunu gösterir, her durumda CI volume'unu kaldırır.

Workflow'un `GITHUB_TOKEN` yetkisi yalnız `contents: read` seviyesindedir.
Kullanılan action'lar taşınabilir etiket yerine tam commit SHA ile sabitlenmiştir.
CI'a model, ham veri veya repository secret verilmez; dolayısıyla gerçek modelle
serving smoke testi yerel teslim doğrulamasında kalır. CI sentetik estimator kullanan
API/UI testleriyle yüzey davranışını doğrular.

## Kaynaklar

- [Docker Compose profiles](https://docs.docker.com/compose/how-tos/profiles/)
- [uv ile Docker](https://docs.astral.sh/uv/guides/integration/docker/)
- [GitHub Actions güvenli kullanım](https://docs.github.com/en/actions/reference/security/secure-use)
