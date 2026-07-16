from __future__ import annotations

from .config import AppConfig, ConfigError, ENTITY_PATTERN


def _m_string(value: str) -> str:
    return value.replace('"', '""')


def generate_m_code(
    config: AppConfig,
    public_url: str,
    entity: str | None = None,
    select_fields: str | None = None,
) -> str:
    base_url = public_url.strip().rstrip("/")
    selected_entity = (entity or config.default_entity).strip()
    selected_fields = config.default_select if select_fields is None else select_fields.strip()

    if not base_url.startswith(("https://", "http://")):
        raise ConfigError("Start the tunnel or provide a valid public URL")
    if not ENTITY_PATTERN.fullmatch(selected_entity):
        raise ConfigError("Entity contains unsupported characters")

    query_line = ""
    if selected_fields:
        query_line = f'        Query = [#"$select" = "{_m_string(selected_fields)}"],\n'

    return (
        "let\n"
        f'    BaseUrl = "{_m_string(base_url)}",\n'
        "    Response = Json.Document(\n"
        "        Web.Contents(BaseUrl, [\n"
        f'            RelativePath = "api/{_m_string(selected_entity)}",\n'
        f"{query_line.replace('        ', '            ', 1)}"
        f'            Headers = [#"x-api-key" = "{_m_string(config.api_key)}"],\n'
        "            Timeout = #duration(0, 0, 2, 0)\n"
        "        ])),\n"
        '    Data = if Value.Is(Response, type record) and Record.HasFields(Response, "value")\n'
        '        then Response[value]\n'
        "        else Response\n"
        "in\n"
        "    Data"
    )
