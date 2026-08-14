import os
import OpenDartReader

# API 키는 환경변수에서 읽음 (export OPENDART_API_KEY="...")
api_key = os.environ.get("OPENDART_API_KEY")
if not api_key:
    raise SystemExit("환경변수 OPENDART_API_KEY가 설정되지 않았습니다.")

dart = OpenDartReader(api_key)

# 브이티 / 2026 반기보고서(11012) / 연결재무제표(CFS)
df_fs = dart.finstate_all(
    corp="브이티",
    bsns_year=2026,
    reprt_code="11012",
    fs_div="CFS",
)

import pandas as pd
pd.set_option("display.max_rows", None)
pd.set_option("display.width", 200)

if df_fs is None or len(df_fs) == 0:
    print("결과 없음: 2026 반기보고서가 아직 제출되지 않았거나 조회되지 않았습니다.")
else:
    print(df_fs[["sj_nm", "account_nm", "thstrm_amount", "frmtrm_amount"]].to_string(index=False))
