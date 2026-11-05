import os

# Testes NUNCA chamam serviços reais. As variáveis são definidas como vazias (e
# não removidas) porque main.py chama load_dotenv(), que só preenche variáveis
# ausentes — assim o .env local com credenciais reais não vaza para os testes.
_REAL_SERVICE_VARS = (
    "WATSONX_URL", "WATSONX_PROJECT_ID", "IBM_CLOUD_API_KEY",
    "CLOUDANT_URL", "CLOUDANT_APIKEY",
    "OPEN_FINANCE_BASE_URL", "OPEN_FINANCE_API_KEY",
    "SERASA_BASE_URL", "SERASA_API_KEY",
)
for var in _REAL_SERVICE_VARS:
    os.environ[var] = ""

# Fontes externas simuladas; Cloudant e Granite são substituídos por fakes injetados.
os.environ["OPEN_FINANCE_SANDBOX"] = "true"
os.environ["SERASA_SANDBOX"] = "true"
# Chave fixa só para testes — precisa existir antes de importar os módulos.
os.environ["PSEUDONYM_HMAC_KEY"] = "ab" * 32
os.environ["APP_ENV"] = "development"
