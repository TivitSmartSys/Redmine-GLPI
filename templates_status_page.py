"""Template do painel HTML gerado por migration_status.py."""

PAGE = r'''<title>Migração Redmine → GLPI</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&family=IBM+Plex+Sans:wght@400;500;600;700&display=swap">
<style>
  :root {{
    --ground:      #EDF0F2;
    --surface:     #FFFFFF;
    --surface-alt: #F5F7F8;
    --line:        #D6DDE1;
    --line-soft:   #E5EAED;
    --ink:         #0F1519;
    --ink-2:       #3A4750;
    --muted:       #64757F;
    --accent:      #0B6E7F;
    --accent-soft: #D8EBEF;
    --ok:          #2F7A57;
    --ok-soft:     #DCEDE4;
    --warn:        #A8461B;
    --warn-soft:   #F6E3D9;
    --unknown:     #7A8894;
    --unknown-soft:#E4E9EC;
    --shadow:      0 1px 2px rgba(15,21,25,.06), 0 8px 24px -12px rgba(15,21,25,.18);

    --sans: "IBM Plex Sans", ui-sans-serif, system-ui, "Segoe UI", Roboto, sans-serif;
    --mono: "IBM Plex Mono", ui-monospace, "SF Mono", Menlo, Consolas, monospace;
  }}

  @media (prefers-color-scheme: dark) {{
    :root:not([data-theme="light"]) {{
      --ground:      #0C1216;
      --surface:     #131B21;
      --surface-alt: #18222A;
      --line:        #27343D;
      --line-soft:   #1E2A32;
      --ink:         #E7EEF1;
      --ink-2:       #B4C3CB;
      --muted:       #8598A3;
      --accent:      #47AEC1;
      --accent-soft: #10333B;
      --ok:          #5FBE8E;
      --ok-soft:     #122E22;
      --warn:        #E08152;
      --warn-soft:   #33190E;
      --unknown:     #8797A2;
      --unknown-soft:#1D272E;
      --shadow:      0 1px 2px rgba(0,0,0,.4), 0 8px 24px -12px rgba(0,0,0,.6);
    }}
  }}

  :root[data-theme="dark"] {{
    --ground:      #0C1216;
    --surface:     #131B21;
    --surface-alt: #18222A;
    --line:        #27343D;
    --line-soft:   #1E2A32;
    --ink:         #E7EEF1;
    --ink-2:       #B4C3CB;
    --muted:       #8598A3;
    --accent:      #47AEC1;
    --accent-soft: #10333B;
    --ok:          #5FBE8E;
    --ok-soft:     #122E22;
    --warn:        #E08152;
    --warn-soft:   #33190E;
    --unknown:     #8797A2;
    --unknown-soft:#1D272E;
    --shadow:      0 1px 2px rgba(0,0,0,.4), 0 8px 24px -12px rgba(0,0,0,.6);
  }}

  * {{ box-sizing: border-box; }}

  body {{
    background: var(--ground);
    color: var(--ink);
    font-family: var(--sans);
    font-size: 15px;
    line-height: 1.55;
    -webkit-font-smoothing: antialiased;
  }}

  .wrap {{
    max-width: 1120px;
    margin: 0 auto;
    padding: 40px 24px 72px;
    display: flex;
    flex-direction: column;
    gap: 28px;
  }}

  /* ---- header ---- */
  .head {{ display: flex; flex-direction: column; gap: 10px; }}

  .eyebrow {{
    font-family: var(--mono);
    font-size: 11px;
    font-weight: 500;
    letter-spacing: .14em;
    text-transform: uppercase;
    color: var(--accent);
  }}

  h1 {{
    margin: 0;
    font-size: clamp(28px, 4vw, 40px);
    font-weight: 600;
    letter-spacing: -.02em;
    text-wrap: balance;
  }}

  .sub {{
    margin: 0;
    max-width: 66ch;
    color: var(--ink-2);
  }}

  .stamp {{
    font-family: var(--mono);
    font-size: 12px;
    color: var(--muted);
  }}

  /* ---- progress ---- */
  .panel {{
    background: var(--surface);
    border: 1px solid var(--line);
    border-radius: 10px;
    box-shadow: var(--shadow);
  }}

  .progress {{ padding: 22px 24px 24px; display: flex; flex-direction: column; gap: 18px; }}

  .progress-top {{
    display: flex;
    flex-wrap: wrap;
    align-items: baseline;
    justify-content: space-between;
    gap: 12px;
  }}

  .progress-figure {{
    font-family: var(--mono);
    font-variant-numeric: tabular-nums;
    font-size: 30px;
    font-weight: 600;
    letter-spacing: -.02em;
  }}
  .progress-figure span {{ color: var(--muted); font-weight: 400; font-size: 20px; }}

  .progress-label {{ font-size: 13px; color: var(--muted); }}

  .bar {{
    height: 10px;
    border-radius: 999px;
    background: var(--surface-alt);
    border: 1px solid var(--line-soft);
    overflow: hidden;
    display: flex;
  }}
  .bar i {{ display: block; height: 100%; background: var(--accent); }}

  .legend {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(190px, 1fr));
    gap: 2px;
    border-top: 1px solid var(--line-soft);
    padding-top: 16px;
  }}
  .leg {{ display: flex; flex-direction: column; gap: 4px; padding-right: 16px; }}
  .leg-name {{
    font-family: var(--mono);
    font-size: 12px;
    color: var(--ink-2);
    display: flex; align-items: center; gap: 7px;
  }}
  .dot {{ width: 8px; height: 8px; border-radius: 2px; flex: none; }}
  .leg-num {{
    font-family: var(--mono);
    font-variant-numeric: tabular-nums;
    font-size: 17px;
    font-weight: 500;
  }}
  .leg-num em {{ font-style: normal; color: var(--muted); font-weight: 400; font-size: 13px; }}

  /* ---- stat tiles ---- */
  .tiles {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
    gap: 12px;
  }}
  .tile {{
    background: var(--surface);
    border: 1px solid var(--line);
    border-radius: 10px;
    padding: 16px 18px;
    display: flex; flex-direction: column; gap: 3px;
  }}
  .tile-k {{
    font-family: var(--mono);
    font-size: 11px;
    letter-spacing: .1em;
    text-transform: uppercase;
    color: var(--muted);
  }}
  .tile-v {{
    font-family: var(--mono);
    font-variant-numeric: tabular-nums;
    font-size: 26px;
    font-weight: 600;
    letter-spacing: -.02em;
  }}
  .tile-n {{ font-size: 12px; color: var(--muted); }}
  .tile.flag {{ border-color: var(--warn); background: var(--warn-soft); }}
  .tile.flag .tile-v, .tile.flag .tile-k {{ color: var(--warn); }}
  .tile.flag .tile-n {{ color: var(--warn); opacity: .85; }}

  /* ---- table ---- */
  .table-head {{
    display: flex; flex-wrap: wrap; align-items: baseline; gap: 10px;
    justify-content: space-between;
    padding: 0 2px;
  }}
  h2 {{
    margin: 0;
    font-size: 17px;
    font-weight: 600;
    letter-spacing: -.01em;
  }}
  .hint {{ font-size: 13px; color: var(--muted); }}

  .scroll {{ overflow-x: auto; border-radius: 10px; border: 1px solid var(--line); background: var(--surface); }}

  table {{ border-collapse: collapse; width: 100%; font-size: 13.5px; }}

  thead th {{
    position: sticky; top: 0;
    background: var(--surface-alt);
    text-align: left;
    font-size: 11px;
    font-family: var(--mono);
    font-weight: 500;
    letter-spacing: .09em;
    text-transform: uppercase;
    color: var(--muted);
    padding: 11px 12px;
    border-bottom: 1px solid var(--line);
    white-space: nowrap;
  }}
  thead th.num {{ text-align: right; }}

  tbody td {{
    padding: 10px 12px;
    border-bottom: 1px solid var(--line-soft);
    vertical-align: baseline;
  }}
  tbody tr:last-child td {{ border-bottom: 0; }}
  tbody tr:hover td {{ background: var(--surface-alt); }}

  td.id, td.num {{
    font-family: var(--mono);
    font-variant-numeric: tabular-nums;
    white-space: nowrap;
  }}
  td.num {{ text-align: right; }}
  td.id {{ color: var(--ink-2); }}

  /* severity stripe: state encoded in form, not only in words */
  td.stripe {{
    padding-left: 12px;
    border-left: 3px solid transparent;
  }}
  tr.is-ok td.stripe      {{ border-left-color: var(--ok); }}
  tr.is-diverge td.stripe {{ border-left-color: var(--warn); }}
  tr.is-nomap td.stripe   {{ border-left-color: var(--unknown); }}

  .mismatch {{ color: var(--warn); font-weight: 600; }}
  .qmark {{ color: var(--unknown); }}

  .pill {{
    display: inline-block;
    font-family: var(--mono);
    font-size: 11px;
    font-weight: 500;
    letter-spacing: .04em;
    padding: 2px 8px;
    border-radius: 999px;
    white-space: nowrap;
  }}
  .pill.ok      {{ background: var(--ok-soft);      color: var(--ok); }}
  .pill.diverge {{ background: var(--warn-soft);    color: var(--warn); }}
  .pill.nomap   {{ background: var(--unknown-soft); color: var(--unknown); }}

  td.ent {{ color: var(--muted); font-size: 12px; white-space: nowrap; }}
  td.name {{ min-width: 260px; }}
  td.when {{ font-family: var(--mono); font-size: 12px; color: var(--muted); white-space: nowrap; }}

  /* ---- notes ---- */
  .notes {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 12px; }}
  .note {{
    background: var(--surface);
    border: 1px solid var(--line);
    border-left: 3px solid var(--accent);
    border-radius: 8px;
    padding: 16px 18px;
  }}
  .note.warn {{ border-left-color: var(--warn); }}
  .note.grey {{ border-left-color: var(--unknown); }}
  .note h3 {{
    margin: 0 0 6px;
    font-size: 13px;
    font-weight: 600;
    letter-spacing: .01em;
  }}
  .note p {{ margin: 0; font-size: 13px; color: var(--ink-2); }}
  .note p + p {{ margin-top: 8px; }}
  .note code {{
    font-family: var(--mono);
    font-size: 12px;
    background: var(--surface-alt);
    border: 1px solid var(--line-soft);
    border-radius: 4px;
    padding: 1px 5px;
  }}

  footer {{
    color: var(--muted);
    font-size: 12.5px;
    border-top: 1px solid var(--line);
    padding-top: 16px;
  }}
  footer code {{ font-family: var(--mono); }}

  a {{ color: var(--accent); }}
  a:focus-visible, tr:focus-visible {{ outline: 2px solid var(--accent); outline-offset: 2px; }}

  @media (prefers-reduced-motion: reduce) {{
    * {{ animation: none !important; transition: none !important; }}
  }}
</style>

<div class="wrap">

  <header class="head">
    <div class="eyebrow">Acompanhamento · fase 1</div>
    <h1>Migração Redmine → GLPI</h1>
    <p class="sub">
      Estado real lido do GLPI, projeto a projeto. As contagens de tarefas, notas e
      arquivos são conferidas contra a árvore de origem no Redmine — quando os dois
      lados divergem, a tabela mostra <span class="mismatch">o encontrado / o esperado</span>.
    </p>
    <div class="stamp">Gerado em {stamp} por <code>migration_status.py --verify</code></div>
  </header>

  <section class="panel progress">
    <div class="progress-top">
      <div>
        <div class="progress-figure">{projects} <span>/ {scope}</span></div>
        <div class="progress-label">raízes migradas · faltam {remaining} · {pct}%</div>
      </div>
      <div class="progress-label" style="text-align:right">
        Fase 0 concluída: 1262 projetos do import de 2026-06-06 removidos,<br>
        marcador <code style="font-family:var(--mono)">rdmfield</code> voltou a ser confiável
      </div>
    </div>

    <div class="bar" role="img" aria-label="{projects} de {scope} raízes migradas">
      <i style="width:{pct}%"></i>
    </div>

    <div class="legend">
      {legend}
    </section>

  <section class="tiles">
    <div class="tile">
      <div class="tile-k">Projetos</div>
      <div class="tile-v">{projects}</div>
      <div class="tile-n">no GLPI, com marcador</div>
    </div>
    <div class="tile">
      <div class="tile-k">Tarefas</div>
      <div class="tile-v">{tasks}</div>
      <div class="tile-n">{untracked} projeto(s) fora da contagem</div>
    </div>
    <div class="tile">
      <div class="tile-k">Notas</div>
      <div class="tile-v">{notes}</div>
      <div class="tile-n">aba Notas</div>
    </div>
    <div class="tile">
      <div class="tile-k">Arquivos</div>
      <div class="tile-v">{documents}</div>
      <div class="tile-n">aba Documentos</div>
    </div>
    <div class="tile flag">
      <div class="tile-k">Divergem</div>
      <div class="tile-v">{diverging}</div>
      <div class="tile-n">conferido contra o Redmine</div>
    </div>
  </section>

  <div class="table-head">
    <h2>Projetos migrados</h2>
    <div class="hint">Ordenado do mais recente para o mais antigo, por id do GLPI.</div>
  </div>

  <div class="scroll">
    <table>
      <thead>
        <tr>
          <th>RDM</th>
          <th class="num">GLPI</th>
          <th class="num">Tarefas</th>
          <th class="num">Notas</th>
          <th class="num">Arquivos</th>
          <th>Confere</th>
          <th>Entidade</th>
          <th>Criado em</th>
          <th>Projeto</th>
        </tr>
      </thead>
      <tbody>
        {rows}
      </tbody>
    </table>
  </div>

  <section class="notes">
    <div class="note warn">
      <h3>Divergências têm três causas — nenhuma é perda silenciosa</h3>
      <p>
        <strong>Arquivo grande demais.</strong> O GLPI aceita 50 MB, mas o
        <code>post_max_size</code> do PHP é menor e recusa depois. Medido em
        2026-08-31: 11,3 MB passa, 16,9 MB não. Esses arquivos continuam no
        Redmine e vão para a lista de envio manual.
      </p>
      <p>
        <strong>Conexão caída no download.</strong> Um
        <code>RemoteDisconnected</code> do Redmine derruba um arquivo que, numa
        segunda tentativa, desce sem problema. Nada a ver com tamanho.
      </p>
      <p>
        <strong>O Redmine andou depois da migração.</strong> Esta tabela compara
        o Redmine <em>de agora</em> com o GLPI <em>de agora</em>, e o Redmine é
        um sistema vivo. Uma nota escrita depois que o projeto foi migrado
        aparece aqui como divergência sem que nada tenha falhado — foi o caso de
        RDM 20586, cujo comentário entrou enquanto o lote rodava.
      </p>
    </div>

    <div class="note grey">
      <h3>“Sem mapa” não quer dizer vazio</h3>
      <p>
        Este GLPI <strong>não lista <code>ProjectTask</code> por nenhuma rota</strong> —
        a rota plana devolve zero para a instância inteira mesmo com tarefas
        existindo e legíveis por id. A única fonte de “quais tarefas são deste
        projeto” é o <code>migration_map</code> local, que é proteção contra
        queda, não autoridade.
      </p>
      <p>
        Para um projeto migrado antes do banco atual o mapa está vazio, e aí a
        contagem certa é <strong>desconhecida</strong>, nunca zero. Relatar zero
        ali inventa uma divergência.
      </p>
    </div>

    <div class="note">
      <h3>Datas de criação: dois grupos</h3>
      <p>
        Os projetos migrados a partir de 2026-08-27 carregam o
        <code>created_on</code> real do Redmine, deslocado de UTC−3. Os
        anteriores — 1286 a 1296 — carregam a data da própria migração, porque
        a função de data só entrou depois. Decisão do responsável: ficam como
        estão.
      </p>
    </div>
  </section>

  <footer>
    Tabela gerada, nunca escrita à mão: <code>python migration_status.py --verify --out reports/STATUS.md</code>.
    Somente leitura — não escreve no GLPI nem no Redmine. O Redmine é fonte, jamais destino.
  </footer>

</div>
'''
