# Pazartesi CadPlot MCP demo akışı

Bu demo yalnız lisanslı şirket iş istasyonunda, sorumlunun izin verdiği anonim veya üretim dışı bir
DWG kopyasıyla yapılır. Şirket DWG/PC3/PMP/CTB/STB/DWT dosyaları kişisel bilgisayara veya GitHub'a
taşınmaz.

## Önceden hazırla

- AutoCAD 2016 ve 2025 için sürüme uygun managed SDK/reference klasörleri;
- bir paftalık yetkili DWG kopyası ve elle kabul edilmiş referans PDF;
- gerçek page setup, plotter, plot style ve canonical media adlarının yazıldığı yerel `config.yaml`;
- kaynaklardan tamamen ayrı yerel `workspace_root`;
- `scripts/build-bundle.ps1` çıktısı ve `scripts/verify-bundle.ps1` hash listesi.

## 10 dakikalık gösterim

1. AutoCAD kapalıyken bundle kurulum hedefini `scripts/install-bundle.ps1 -WhatIf` ile gösterin.
2. İlk açılışta publish kapalı kalsın. `get_autocad_plugin_status` ile doğru adapter, `ACADVER`,
   `workspaceConfigured=true` ve `publishEnabled=false` değerlerini gösterin.
3. `inspect_drawing` çalıştırıp bulunan çerçeve, mevcut layout, page setup, plotter, media ve stili
   ekranda karşılaştırın.
4. `create_publish_plan` çalıştırın. Ölçek, yön, plot window, hedef layout ve blocker listesini
   sorumluya okutun.
5. Plan ID açıkça kabul edildikten sonra `stage_publish_job` çalıştırın. Orijinal yerine hash'i
   doğrulanmış izole kopya oluştuğunu gösterin.
6. `validate_staged_job` ile aynı manifesti AutoCAD eklentisine bağımsız doğrulatın.
7. Sorumlu gerçek yazma pilotuna izin verirse AutoCAD'i kapatın,
   `CADPLOT_ENABLE_PUBLISH=1` ayarlayıp yeniden açın. Önceden açık AutoCAD oturumunda çevre
   değişkeni etkili sayılmaz.
8. Yalnız bir paftayı, staging sonucundaki tam `plan_id + manifest_sha256` ile sıraya alın.
9. Canlı durum `Succeeded` olduktan sonra `read_publish_receipt` ve
   `audit_publish_outputs` çalıştırın. Kabul sonucu yalnız `publish_verified=true` ise geçer.
10. PDF'yi referansla yan yana açıp yön, crop, gerçek ölçek, lineweight, CTB/STB, font ve title
    block kontrolünü sorumluya yaptırın.

## Söylenecek net sınırlar

- 2024 API compile probe, 2016 veya 2025 canlı uyumluluk kanıtı değildir.
- 2016 sonucu 2025'i; 2025 sonucu 2016'yı kanıtlamaz. İki pilot ayrı kaydedilir.
- Geçerli PDF dosyasının tek başına varlığı AutoCAD yürütme kanıtı değildir; başarılı receipt de
  gerekir.
- Profilde `template_layout` yoksa executor boş layout + tam sayfa viewport kurar. Kaynak DWG
  içinde tek floating viewport'lu onaylı template varsa onu klonlayabilir. Harici DWT/DWG importu
  gerekiyorsa yetkili örnek incelenmeden bu destek tamamlandı denmez.
- Normal ChatGPT web oturumu yerel AutoCAD'e kendiliğinden erişmez. İlk demo yerel MCP istemcisiyle
  yapılır; merkezi şirket ChatGPT bağlantısı IT onaylı güvenli connector/tunnel işidir.

## Pilot başarısız olursa

Hata kodunu, manifest/receipt hash'lerini ve çıktı audit'ini kaydedin. Var olan PDF'nin veya job'ın
üstüne yazmayın. Nedeni düzeltip yeni plan onayıyla yeni bir staging job oluşturun. Canlı kabul
geçmeden “production ready” demeyin; çalışan dry-run ve güvenlik zincirini yine gösterebilirsiniz.
