"""Cliente síncrono do Cloudant — compartilhado por todos os repositórios do M4."""

from src.core.config import CLOUDANT_VARS, env, require_env


def build_cloudant_client():
    """Cria e retorna um CloudantV1 autenticado via IAM a partir de CLOUDANT_URL/CLOUDANT_APIKEY."""
    from ibm_cloud_sdk_core.authenticators import IAMAuthenticator
    from ibmcloudant.cloudant_v1 import CloudantV1

    require_env(*CLOUDANT_VARS)
    client = CloudantV1(authenticator=IAMAuthenticator(env("CLOUDANT_APIKEY")))
    client.set_service_url(env("CLOUDANT_URL"))
    return client
