# Aşama 5 — PostgreSQL ve SQL özellik hattı

Aşama tamamlandı. Yerel PostgreSQL, Docker Compose üzerinden çalışıyor.

## Doğrulanan sonuçlar

- PostgreSQL sürümü: **18.6 (Debian 18.6-1.pgdg13+2)**.
- Ham kayıt: **41.188**.
- Sabit ayrım kaydı: **41.188**.
- SQL özellik görünümü: **41.188 satır**, row_id + **9 model özelliği**.
- Tüm 21 ham kaynak sütunu, kayıt kimlikleri ve veri parmak izleri doğrulandı.
- Dokuz SQL özelliği Python çıktısıyla birebir eşleşti.
- Eğitim/doğrulama/test ve mükerrer kayıt metadata'sı eşleşti.
- Tekrar yüklemede kayıt sayısı ve ilk yükleme zamanı korundu.
- Konteyner aynı kalıcı volume ile yeniden oluşturulduktan sonra veri doğrulaması geçti.

| Ayrım | Atanan | Kullanılan | Dışlanan tekrar |
|---|---:|---:|---:|
| Eğitim | 24.712 | 24.705 | 7 |
| Doğrulama | 8.238 | 8.235 | 3 |
| Test | 8.238 | 8.236 | 2 |

## Veritabanı nesneleri

- `campaign.dataset_loads`: kaynak/split parmak izleri ve ilk yükleme kaydı.
- `campaign.raw_records`: 21 kaynak alanı, row_id ve kaynak parmak izi.
- `campaign.split_assignments`: sabit ayrımlar ve tekrar kayıt bilgisi.
- `campaign.model_features`: row_id ve dokuz onaylı özellik; y/duration yok.
- `campaign.training_metadata`: hedef ve ayrım bilgileri; özelliklerden ayrı.

## PyCharm terminali

```bash
docker compose up -d --wait db
python -m bank_campaign.data.database load
python -m bank_campaign.data.database verify
```

`load`, veriyi bir transaction içinde COPY/staging üzerinden yükler. Tekrar
çalıştırmak satır eklemez. Farklı kaynak/split veya mevcut içerik uyuşmazlığında
hata verir; kısmi değişiklikler geri alınır. `verify`, veritabanına yazmadan
ham veriyi, özellikleri ve metadata'yı kontrol eder.

Bağlantı: 127.0.0.1:55432; veritabanı/kullanıcı: bank_campaign.
Parola özel `.env` dosyasındadır; bu rapora dahil edilmedi.
PostgreSQL imajı digest ile sabitlendi. Python tarafında ek bağımlılık kurulmadı.

## Test kanıtı

- Entegrasyon etkin tam test çalışması: **154 geçti**.
- Bu sayının içinde **8 gerçek PostgreSQL entegrasyon testi** var.
- Tekrar yükleme, SQL/Python eşleşmesi, transaction rollback, farklı kaynak reddi,
  duration/contact/ekonomik alan bozulmasının yakalanması test edildi.
- Ruff lint/format, mypy, uv lock ve Compose yapılandırma kontrolleri geçti.
- Entegrasyon testleri uygulama veritabanı yerine kendi geçici veritabanlarını kullanır.
- Bağımsız kod incelemesindeki ham alan doğrulaması eksikliği giderildi ve yeniden doğrulandı.

Makine tarafından okunabilir kanıt proje içindeki
`reports/database_verification.json` dosyasındadır.
SQL şeması ve örnek sorgular `sql/` ve `docs/database.md` içindedir.

Sıradaki aşama: FastAPI tahmin ve kampanya seçimi servisi.
