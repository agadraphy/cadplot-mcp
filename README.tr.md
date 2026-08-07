# CadPlot MCP

CadPlot MCP; çok sayıdaki revize DWG dosyasını denetlenebilir biçimde incelemek, pafta
çerçevelerini şirket page setup'larıyla eşleştirmek, ölçek/layout kararlarını planlamak ve PDF
çıktılarını doğrulamak için geliştirilen açık kaynak bir MCP sunucusudur.

## Mevcut durum

Hazır olan parçalar:

- yalnızca izin verilen klasörlerde DWG tarama;
- çalışan AutoCAD üzerinden salt-okunur layout, page setup ve çerçeve inceleme;
- `70x100`, `700x1000 mm` ve `A0-A5` gibi kâğıt etiketi tanıma;
- çerçeve geometrisinden plot penceresi, yön ve izinli ölçek türetme;
- PC3 ile CTB/STB değerlerini şirket profiliyle karşılaştırma;
- hash'li ve onay kapılı dry-run planı;
- 50 DWG'ye kadar sayfalı, kaldığı yerden devam edebilen batch planlama;
- orijinale dokunmadan izole çalışma klasörüne doğrulanmış DWG kopyası alma;
- beklenen PDF'leri yol, gerçek PDF yapısı, tek sayfa, sayfa ölçüsü, boyut ve SHA-256 açısından
  denetleme;
- AutoCAD 2016 ve 2025-2026 için ayrı .NET adaptör/bundle yapısı;
- varsayılan kapalı, ana AutoCAD thread'inde çalışan sınırlı yayın kuyruğu;
- staged DWG içinde bellekte layout/page setup/viewport kurup her pafta için ayrı PDF üreten
  ortak executor.

Ortak executor, kurulu AutoCAD 2024 API DLL'lerine karşı AutoCAD açılmadan derlenmiştir. Henüz
tamamlanmamış kapı: aynı kodun sürüme uygun Autodesk SDK referanslarıyla paketlenip lisanslı
AutoCAD 2016 ve 2025 üzerinde yetkili örnek DWG ve şirket plot kaynaklarıyla canlı kabul testinden
geçmesidir. Bu doğrulama yapılmadan proje üretim-hazır olarak sunulmaz.

## Güvenlik modeli

- Keyfi AutoLISP veya AutoCAD komutu çalıştırılmaz.
- Kaynak DWG'nin üstüne yazılmaz, dosya taşınmaz veya silinmez.
- Her plan kaynak DWG'nin SHA-256 parmak izine bağlıdır.
- Plan değişirse veya DWG onaydan sonra değişirse işlem reddedilir.
- Hazır olmayan plan stage edilemez.
- Var olan layout adıyla çakışma blocker üretir; layout ezilmez.
- Eklenti staged DWG'yi çalıştırmadan hemen önce tekrar hash'ler.
- Layout ve viewport değişiklikleri yalnız bellekte tutulur; PDF'den sonra DWG kaydedilmeden
  kapatılır ve final audit için byte-byte aynı kalır.
- Var olan PDF'nin üstüne yazılmaz; meşgul PlotEngine veya hatalı sayfa ölçüsü işi durdurur.
- `CADPLOT_ENABLE_PUBLISH=1` açıkça verilmedikçe gerçek yayın komutu kapalıdır.
- Çalışma alanı symlink/junction üzerinden yönlendirilemez.
- Şirket DWT, PC3, PMP, CTB/STB veya DWG dosyaları repoya eklenmez.

## Yerel kurulum

Gereksinimler: Windows, Python 3.11+, `uv` ve canlı inceleme için AutoCAD.

```powershell
git clone https://github.com/YOUR-USER/cadplot-mcp.git
cd cadplot-mcp
uv sync --extra autocad --extra dev
Copy-Item examples/config.example.yaml config.yaml
$env:CADPLOT_CONFIG = "$PWD\config.yaml"
uv run cadplot-mcp
```

`config.yaml` içindeki `allowed_roots`, `workspace_root`, page setup, plotter, plot style, çizim
birimi ve izinli ölçekler ofisin gerçek standardına göre düzenlenmelidir.
Özel PC3 kâğıtlarında `canonical_media`, AutoCAD'in bildirdiği değerle aynı yazım ve büyük/küçük
harf kullanılarak girilmelidir.
`pdf_page_tolerance_mm`, üretilen PDF'nin fiziksel sayfa ölçüsü kontrolünü belirler ve güvenlik
nedeniyle 10 mm'yi geçemez.
`minimum_frame_confidence` varsayılan olarak `0.85` değerindedir; nested veya belirsiz çerçeveler
bu eşiğin altında otomatik yayına alınmaz.
Ofisin güvenilir bir çerçeve layer standardı varsa `frame_layers` listesi doldurulabilir; diğer
layer'lardaki adaylar blocker olur.
Her kaynak DWG içinde onaylı bir paper-space title-block layout'u ve tam bir floating viewport
varsa profile `template_layout` eklenebilir. Eklenti layout'u staged kopya içinde klonlar, pafta
geometrisini korur ve klon viewport'unu onaylı pencere/ölçeğe taşır. Eksik veya çok viewport'lu
template işi durdurur; harici DWT yolu kendiliğinden kabul edilmez.

AutoCAD kullanmadan plan→onay→kopya staging→PDF audit zincirini denemek için:

```powershell
uv run python scripts/run-synthetic-demo.py
```

Bu sentetik test gerçek DWG/AutoCAD kabul testi yerine geçmez.
2016 ve 2025 canlı sonuçları ayrı JSON kayıtları olarak tutulur ve
`scripts/validate-pilot-evidence.py` ile birlikte doğrulanmadan üretim kabulü verilmez.

## Önerilen pilot akışı

Sunumda doğrudan kullanmak için [Pazartesi demo runbook](docs/pazartesi-demo-tr.md) dosyasına bakın.

1. Lisanslı AutoCAD'i açın ve modal pencere bırakmayın.
2. Yalnızca izinli, anonimleştirilmiş bir DWG kopyasını `allowed_roots` altına koyun.
3. `validate_environment` ve `get_autocad_plugin_status` çalıştırın.
4. `inspect_drawing` ile layout/page setup/çerçeve sonuçlarını inceleyin.
5. `create_publish_plan` sonucundaki blocker'ları çözün.
6. `preview_publish_plan` ile aynı hash'li planı eklentiye doğrulatın.
7. Plan kimliğini açıkça onaylayarak `stage_publish_job` çağırın.
8. `validate_staged_job` ile manifesti eklentinin bağımsız workspace ayarına doğrulatın.
9. Lisanslı pilotta aynı `plan_id` ve staging sonucundaki `manifest_sha256` değerlerini
   `queue_publish_job` aracına açıkça verin.
10. `get_publish_job_status` sonucu `Succeeded` olana kadar durumu okuyun.
11. `read_publish_receipt` ile manifest hash'ine bağlı kalıcı başarı kanıtını doğrulayın.
12. Üretilen dosyaları `audit_publish_outputs` ile doğrulayın; ancak hem PDF'ler hem receipt
    geçerliyse dönen `publish_verified=true` sonucunu kabul edin.

300 çizim için `create_batch_publish_plans` aracını varsayılan 20'lik sayfalarla kullanın ve
`has_more=false` olana kadar dönen `next_offset` değeriyle devam edin.
Hazır planları kopya çalışma alanına almak için en fazla 20 benzersiz `(path, plan_id)` onayını
`stage_publish_batch` aracına verin. Bir dosyanın değişmesi diğer geçerli dosyaları silmez veya
orijinalleri değiştirmez; her sonuç ayrı raporlanır.
Staging sonuçlarındaki benzersiz `(manifest_path, plan_id, manifest_sha256)` üçlülerini yine en
fazla 20'şer adet `queue_publish_batch` çağrısıyla sıraya alın; 300 işi tek bir MCP çağrısına
doldurmayın.
AutoCAD kapanınca canlı kuyruk durumu silinir; terminal sonuçtaki `receipt.json` silinmez.
Yeniden başladıktan sonra `read_publish_receipt` ile kaldığınız işi güvenle doğrulayabilirsiniz.
Tüm çalışma alanını kaldığınız yerden taramak için `create_publish_operations_report` çağrısını
`has_more=false` olana kadar `next_after_job_id` ile sayfalayın. Her `report_page_id` bir kontrol
noktasıdır. Yalnız `awaiting_execution` işlerinde yeniden sıra onayı döner; önce canlı durum bakılır.

## ChatGPT bağlantısı

Yerel MCP istemcisi, Python sunucusunu `stdio` ile aynı Windows bilgisayarda çalıştırabilir ve
yerel AutoCAD eklentisine named pipe üzerinden ulaşabilir.
Repo içindeki doğrulanmış opsiyonel Codex wrapper'ı
[`integrations/codex/cadplot-mcp`](integrations/codex/cadplot-mcp/README.md) klasöründedir; yalnız
önceden kurulmuş `cadplot-mcp` komutunu başlatır ve şirket dosyalarını paketlemez.

AutoCAD başlatılmadan önce aynı terminal/başlatıcı ortamında `CADPLOT_WORKSPACE_ROOT`, Python
ayarındaki `workspace_root` ile aynı klasöre ayarlanmalıdır. Eklenti güvenilir workspace değerini
MCP isteğinden kabul etmez.
İnceleme ve staging doğrulaması sırasında publish kapalı bırakılmalıdır. Yalnız yetkili canlı
pilot için AutoCAD başlatılmadan önce `CADPLOT_ENABLE_PUBLISH=1` ayarlanır; değişiklikten sonra
AutoCAD yeniden başlatılır.

`workspace_root`, kaynak `allowed_roots` klasörlerinden tamamen ayrı olmalıdır; iç içe klasörler
reddedilir. Yanlış yazılmış config alanları, boş AutoCAD kaynak adları ve `NaN/Infinity` değerleri
iş başlamadan hata verir.

Yükseltmede AutoCAD'i kapatın; önce `scripts/uninstall-bundle.ps1 -WhatIf` ile tam hedefi görün.
Script yalnız CadPlot MCP adı ve ProductCode'u eşleşen paketi kaldırır, junction üzerinden silmez.

Merkezi ChatGPT Business/Enterprise/Edu ortamında ise şirket yöneticisinin MCP bağlantısını
onaylaması ve merkezi servis ile AutoCAD iş istasyonu arasında yönetilen güvenli bağlantı kurması
gerekir. İnternete named pipe, AutoCAD COM veya korumasız yerel port açılmaz. İlk pilot için yerel
kurulum daha hızlı ve daha düşük risklidir.

## Lisans

MIT. Autodesk ve AutoCAD, Autodesk Inc. markalarıdır. Bu proje Autodesk tarafından üretilmemiş
veya onaylanmamıştır.

Teknik ayrıntılar ve İngilizce dokümantasyon için [README.md](README.md) dosyasına bakın.
