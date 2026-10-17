"""Local Turkish campaign workspace; all inference is delegated to FastAPI."""

import hashlib
import json
import os
from pathlib import Path

import pandas as pd
import streamlit as st

from bank_campaign.dashboard import ApiClient, ApiError, parse_candidate_csv, selected_csv
from bank_campaign.schemas import CampaignRequest, CampaignResponse, ScoreRequest

ROOT = Path(__file__).resolve().parents[1]
API_URL = os.environ.get("BANK_CAMPAIGN_API_URL", "http://127.0.0.1:8000")
PUBLIC_API_URL = os.environ.get("BANK_CAMPAIGN_PUBLIC_API_URL", API_URL)

st.set_page_config(
    page_title="Kampanya çalışma alanı", page_icon=":material/table_view:", layout="wide"
)


def history() -> None:
    st.title("Tarihsel değerlendirme")
    st.write(
        "Dondurulmuş deneyin kayıtlı sonuçları. Bu ekran yüklediğiniz adayları değerlendirmez."
    )
    try:
        test = json.loads((ROOT / "reports/test_metrics.json").read_text())
        comparison = pd.read_csv(ROOT / "reports/model_comparison.csv")
    except (OSError, ValueError) as exc:
        st.error(
            "Değerlendirme raporları okunamadı. reports klasöründeki kayıtlı raporları kontrol edin."
        )
        st.caption(type(exc).__name__)
        return
    st.subheader("Doğrulama bölümünde model seçimi")
    st.write("Model, doğrulama Average Precision ölçütüne göre seçildi: lightgbm_small.")
    st.dataframe(comparison, hide_index=True, width="stretch")
    st.subheader("Dondurulmuş test sonucu")
    st.caption(
        f"{test['test_records']:,} tarihsel kayıt · model: {test['selected_model']} · yeniden hesaplanmadı"
    )
    st.dataframe(pd.DataFrame([test["model_metrics"]]), hide_index=True, width="stretch")
    scenario = test["scenario_results"][0]
    benchmark = pd.DataFrame(
        [
            {
                "Yöntem": "Model",
                "Seçilen kayıt": scenario["selected_count"],
                "Tarihsel abonelik": float(scenario["observed_subscriptions"]),
            },
            {
                "Yöntem": "Rastgele (200 tekrar ortalaması)",
                "Seçilen kayıt": test["random_benchmark"]["selected_count"],
                "Tarihsel abonelik": test["random_benchmark"]["observed_mean"],
            },
            {
                "Yöntem": "Sabit geçmiş kuralı",
                "Seçilen kayıt": test["business_rule"]["selected_count"],
                "Tarihsel abonelik": float(test["business_rule"]["observed_subscriptions"]),
            },
        ]
    )
    st.subheader(f"Kayıtlı kapasite karşılaştırması · K = {test['scenario']['capacity']}")
    st.dataframe(benchmark, hide_index=True, width="stretch")
    st.warning(
        "Sabit geçmiş kuralı bu testte modelden daha iyi sonuç verdi. Olasılıklar kalibre edilmemiştir; tarihsel abonelikler aramanın nedensel etkisini veya gerçekleşmiş kârı göstermez."
    )
    st.caption(
        "Bu rapor sabittir. Kampanya ekranındaki kapasite ve maliyet ayarları buradaki sonuçları değiştirmez."
    )


def campaign() -> None:
    st.title("Kampanya çalışma alanı")
    st.write(
        "Aday listesini hazırlayın, arama kapasitesini belirleyin ve öncelikli kayıtları indirin."
    )
    st.caption(
        "Olasılıklar kalibre edilmemiştir. Beklenen abonelik ve net değer, model ile girdiğiniz varsayımlara dayanır."
    )
    source = st.radio("Aday kaynağı", ["Örnek adaylar", "CSV yükle"], horizontal=True, key="source")
    records: list[dict] | None = None
    if source == "Örnek adaylar":
        records = json.loads((ROOT / "examples/score_request.json").read_text())["records"]
        st.caption("Üç sentetik kayıt; gerçek müşteri verisi değildir.")
    else:
        upload = st.file_uploader(
            "Aday CSV dosyası",
            type=["csv"],
            key="candidate_upload",
            help="UTF-8, virgülle ayrılmış. En fazla 2 MiB / 10.000 kayıt. Şablondaki 10 sütunu kullanın.",
        )
        if upload is not None:
            try:
                records = parse_candidate_csv(upload.getvalue())
            except ValueError as exc:
                st.error(str(exc))
        else:
            st.info(
                "CSV şablonunu indirin, resmi kategori değerleriyle doldurun ve buraya yükleyin."
            )
    template = (
        pd.DataFrame(json.loads((ROOT / "examples/score_request.json").read_text())["records"])
        .to_csv(index=False)
        .encode("utf-8-sig")
    )
    st.download_button(
        "Örnek CSV şablonunu indir",
        data=template,
        file_name="campaign_candidates.csv",
        mime="text/csv",
        key="template_download",
    )
    if records is not None:
        # Samples and CSV both obey the same request contract before submission.
        records = [
            row.model_dump() for row in ScoreRequest.model_validate({"records": records}).records
        ]
        st.subheader(f"Adaylar · {len(records):,} kayıt")
        st.dataframe(pd.DataFrame(records), hide_index=True, width="stretch")
        if not records:
            st.info("Dosyada aday yok. Sonuç üretmek için şablona en az bir kayıt ekleyin.")
    st.subheader("Kampanya varsayımları")
    capacity_column, cost_column, value_column = st.columns(3)
    with capacity_column:
        capacity = st.number_input(
            "Arama kapasitesi (K)", min_value=0, max_value=10_000, value=2, step=1, key="capacity"
        )
    with cost_column:
        cost = st.number_input(
            "Kayıt başına maliyet (C)",
            min_value=0.0,
            max_value=1_000_000_000_000.0,
            value=3.0,
            step=1.0,
            key="cost",
        )
    with value_column:
        value = st.number_input(
            "Abonelik başına değer (V)",
            min_value=0.0,
            max_value=1_000_000_000_000.0,
            value=100.0,
            step=10.0,
            key="value",
        )
    mode_label = st.radio(
        "Seçim politikası",
        ["Kapasiteye kadar seç", "Yalnız pozitif beklenen değer"],
        key="mode",
    )
    mode = "capacity" if mode_label == "Kapasiteye kadar seç" else "positive_value"
    st.caption(
        "Pozitif değer politikası yalnız p × V − C > 0 kayıtları seçer. Kayıt sayısı kapasiteden az olabilir. C ve V aynı para biriminde olmalıdır."
    )
    payload = CampaignRequest.model_validate(
        {"records": records or [], "capacity": capacity, "cost": cost, "value": value, "mode": mode}
    )
    fingerprint = (
        hashlib.sha256((source + API_URL + payload.model_dump_json()).encode()).hexdigest()
        if records
        else None
    )
    if st.session_state.get("result_fingerprint") != fingerprint:
        st.session_state.pop("campaign_result", None)
        st.session_state.pop("result_fingerprint", None)
    if st.button(
        "Kampanya listesini oluştur", type="primary", disabled=not records, key="select_campaign"
    ):
        st.session_state.pop("campaign_result", None)
        st.session_state.pop("result_fingerprint", None)
        try:
            with st.spinner("Adaylar skorlanıyor ve kapasite politikası uygulanıyor…"):
                with ApiClient(API_URL) as client:
                    result = client.select(payload)
            st.session_state["campaign_result"] = result.model_dump()
            st.session_state["result_fingerprint"] = fingerprint
        except ApiError as exc:
            st.error(str(exc))
    saved = st.session_state.get("campaign_result")
    if saved is None:
        return
    result = CampaignResponse.model_validate(saved)
    st.divider()
    st.subheader("Öncelikli arama listesi")
    st.caption("Model sürümü")
    st.code(result.model_version, language=None)
    summary = result.summary
    st.markdown(
        f"**Seçilen kayıt:** {summary.selected_count}  \n"
        f"**Beklenen abonelik (model tahmini):** {summary.expected_subscriptions:.4f}  \n"
        f"**Varsayımsal toplam maliyet:** {summary.assumed_cost:.2f}  \n"
        f"**Varsayımsal beklenen net değer:** {summary.assumed_net_value:.2f}"
    )
    if not result.selected:
        st.info(
            "Bu ayarlarla kayıt seçilmedi. Kapasiteyi ve pozitif değer için maliyet/değer varsayımlarını kontrol edin."
        )
    st.dataframe(
        pd.DataFrame([row.model_dump() for row in result.selected]),
        hide_index=True,
        width="stretch",
    )
    st.download_button(
        "Seçilen listeyi CSV indir",
        data=selected_csv(result),
        file_name="campaign_selection.csv",
        mime="text/csv",
        key="selection_download",
    )
    st.caption(
        "İndirilen liste API sırasını ve tam sayısal değerleri korur. Eşit olasılıklar row_id ile çözülür. row_id teknik kayıt anahtarıdır."
    )


with st.sidebar:
    st.header("Bank Campaign")
    page = st.radio("Çalışma alanı", ["Kampanya hazırla", "Tarihsel değerlendirme"], key="page")
    st.divider()
    st.caption("Yerel portföy prototipi")
    if st.button("API durumunu kontrol et", key="check_api"):
        try:
            with ApiClient(API_URL, timeout=3) as client:
                health = client.health()
                if not health.model_loaded:
                    raise ApiError(
                        "Model hazır değil. API model dosyalarını kontrol edip servisi yeniden başlatın."
                    )
                info = client.model_info()
            st.success("Model hazır")
            st.caption(info.model_version)
        except ApiError as exc:
            st.error(str(exc))
    st.link_button("API belgelerini aç", f"{PUBLIC_API_URL}/docs")

if page == "Tarihsel değerlendirme":
    history()
else:
    campaign()
