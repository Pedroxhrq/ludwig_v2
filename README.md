# Ludwig v2

[English](#english) | [Português brasileiro](#portugues-brasileiro)

## English

### Purpose and scope

Ludwig is a local Python 3.15 application for assisted inspection of Brazilian
identity-document images. It uses Portuguese OCR to find **issuing-state
evidence**, reports uncertainty, and optionally displays the oriented document
beside a historical perforation reference for manual comparison.

The supported output states are **SC, SP, PR and RS**. Other Brazilian UFs are
detected as unsupported issuers. This is a rule-based educational prototype:
recognizing an issuer does not establish authenticity, and no specific RG
generation or CIN model has been validated on a representative real dataset.

Inputs are single-frame **JPEG or PNG** images with legible issuing context.
PDFs, camera capture, perspective correction, automatic document segmentation,
and automatic perforation verification are outside the implemented scope.
The default input `di.jpg` is not included.

### Quick start

Install Python 3.15 and [Tesseract with Portuguese language data](https://tesseract-ocr.github.io/tessdoc/Installation.html).
Check that `tesseract --list-langs` includes `por`.
From this project's directory, use PowerShell on Windows:

```powershell
py -3.15 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock
.\.venv\Scripts\python.exe ludwig.py "path\to\document.jpg" --no-display
```

On Linux/macOS, with Python 3.15 and Tesseract already installed:

```sh
python3.15 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
.venv/bin/python ludwig.py "path/to/document.jpg" --no-display
```

If the Windows launcher is unavailable, substitute the full Python 3.15
executable path for `py -3.15`. Activation is optional; these commands
use the virtual environment directly. `.python-version` records the target
version, but does not install Python.

Tesseract is a separate installation; `pip` installs only the Python wrapper.
Language-data guidance is in the [Tesseract documentation](https://tesseract-ocr.github.io/tessdoc/Data-Files.html).
For a Windows installation outside `PATH`:

```powershell
& "C:\Program Files\Tesseract-OCR\tesseract.exe" --list-langs
.\.venv\Scripts\python.exe ludwig.py "document.jpg" --json --tesseract-cmd "C:\Program Files\Tesseract-OCR\tesseract.exe"
```

### Commands and results

Examples below use an activated environment's `python`; the explicit
interpreter paths above work equally well.

```sh
python ludwig.py document.jpg --no-display
python ludwig.py document.jpg --json
python ludwig.py document.jpg --rotation 180 --ocr-timeout 45
python ludwig.py document.jpg --crop "0.15,0.69,0.77,0.80"
python ludwig.py --help
```

| Argument | Behavior | Default |
| --- | --- | --- |
| `image` | JPEG/PNG path, relative to the working directory | `di.jpg` |
| `--no-display` | Print results without loading references or opening windows | Off |
| `--json` | Emit structured results; implies no display | Off |
| `--tesseract-cmd PATH` | Tesseract executable location | Resolve from `PATH` |
| `--rotation auto|0|90|180|270` | Clockwise correction after EXIF orientation | `auto` |
| `--ocr-timeout SECONDS` | Shared OCR budget for all rotations; finite, greater than 0 and at most 300 | 30 |
| `--min-confidence SCORE` | Minimum Tesseract line score, 0–100 | 60 |
| `--crop x0,y0,x1,y1` | Manual comparison crop on the oriented image, normalized coordinates | Whole image |

In desktop mode, focus an OpenCV window and press a key to finish. Cropping
affects only the comparison display; OCR always examines the full image.
References are located beside `ludwig.py`, independent of the working directory.

| Status | Meaning |
| --- | --- |
| `recognized` | One supported issuer candidate meets the OCR threshold |
| `ambiguous` | Conflicting issuing-state candidates, including across rotations |
| `unsupported` | One issuer is found outside SC/SP/PR/RS |
| `low_confidence` | One candidate is found, but its OCR score is too low |
| `unknown` | No readable issuing-state evidence matches the rules |
| `error` | Input, OCR, timeout or display failure |

Exit **0** means a supported issuer was recognized and processing completed;
**1** means the detector abstained; **2** means an operational or argument error.
Help also exits 0. Human-readable output is Portuguese, with English CLI help.
Normal errors go to stderr. JSON mode emits a single object on stdout, including
operational errors; argparse usage errors still go to stderr.

JSON contains `status`, `state` (an object with code/name/description, or null),
`candidates`, `confidence`, `rotation`, and `evidence`.
Evidence contains only UF codes, scores and rotations. It does not include raw
OCR text, names, document numbers, or image pixels. Operational error objects
instead contain `status: "error"` and `error`.

### Recognition rules and image processing

1. Read at most **20 MiB** of encoded data plus one byte for limit detection.
   Inspect image headers before pixel decoding: each dimension must be at least
   32 and at most 12,000 pixels, with at most **20 million pixels** in total.
   Reject animated/multiframe input and unsupported formats.
2. Apply EXIF orientation and composite transparency on white. Convert to
   grayscale; preserve aspect ratio, cap the longest OCR side at 2,000 pixels
   and upscale by no more than 2×.
3. Run Tesseract with Portuguese (`por`) and `--psm 6`. Automatic mode checks
   0°, 90°, 180° and 270° under one deadline. All attempted angles must complete;
   a timeout does not return an earlier partial success. The budget covers OCR
   preprocessing and calls, not file loading or interactive display.
4. Normalize accents, case and whitespace. Require an issuer heading such as
   `ESTADO DE`, `ÓRGÃO EXPEDIDOR`, `UF DE EMISSÃO`, or a recognized
   agency heading. Match complete state names or UF tokens within that context.
   A bare heading can consume one immediate following line in the same OCR
   paragraph. Birthplace, address, name and other recognized personal-field
   labels terminate issuing context.
5. Combine candidates across rotations. Conflicting candidates cause abstention,
   even when one has a weaker score. For one candidate, use its strongest
   observed line score and orientation; accept only if the score meets the
   configured threshold.

Examples covered by the regression corpus:

| OCR text | Result |
| --- | --- |
| `ESTADO DO PARANÁ; NATURALIDADE: SANTA CATARINA` | PR |
| `ESTADO DE MINAS GERAIS; NATURALIDADE: SÃO PAULO` | Unsupported issuer |
| `ÓRGÃO EXPEDIDOR: SSP/SP` | SP |
| `ESTADO DO PARANÁ` plus `ESTADO DE SANTA CATARINA` | Ambiguous |
| `PARANAPANEMA`, standalone `SÃO PAULO`, or birthplace alone | Unknown |

The score is the minimum token confidence on the OCR line, including other
tokens on that line. Wrapped headings retain the lower line score. It is
**not a calibrated probability of issuer correctness**. Plain-text API calls
have no OCR score and report null confidence. Misspelled names such as
`SA0 PAULO` are not silently corrected. Missing labels, unusual layouts,
skew, shadows and OCR field-order errors can still cause rejection or mistakes.

### Supported states and comparison references

| State | Code | Historical descriptions printed | Reference |
| --- | --- | --- | --- |
| Santa Catarina | SC | SSPSC, IGP-SC, SSP, IISC | [SC.jpg](SC.jpg) |
| São Paulo | SP | IIRGD | [SP.jpg](SP.jpg) |
| Paraná | PR | SESP, IIPR | [PR.jpg](PR.jpg) |
| Rio Grande do Sul | RS | IIRS, DIRS | [RS.jpg](RS.jpg) |

Agency strings are inherited reference descriptions, not a verified current
directory or a claim that every abbreviation applies to every document model.
The four small photographs are illustrative; there is no automatic similarity
score or universal perforation crop. The default display is the whole oriented
document. A manual crop requires `0 <= x0 < x1 <= 1` and
`0 <= y0 < y1 <= 1`, producing at least 2 pixels per dimension.

The [2019 booklet](<Apostila/Documentoscopia - Na Verificação Rápida de Documentos (2019).pdf>)
is supplementary material and is never loaded by the program. Provenance,
permissions and model applicability are recorded in
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md); they remain unverified where
the original repository supplies no evidence.

### Tests and measured baseline

```sh
python -m unittest discover -s tests -v
python -m pip check
python benchmark.py --synthetic --check --output benchmarks/local-synthetic.json
```

Unit tests exercise classification, image limits before decoding, EXIF, rotation,
crop validation, timeout budgets, error handling, JSON output, reference selection
and benchmark metrics. They mock OCR/windows and need no Tesseract executable or
desktop. The [32 labeled text cases](benchmarks/text_cases.json) cover supported,
unsupported, ambiguous, birthplace-only and damaged-text examples.

The separate benchmark runs **real Tesseract** on **40 generated images**:
32 supported examples (four states × upright/90°/180°/270°/blur/margins/low
contrast/JPEG artifacts), plus eight unknown, ambiguous and unsupported examples.
They contain invented text, no personal identities, and no scans from the booklet.

The recorded [baseline](benchmarks/baseline.json), using Python 3.15.0 and
Tesseract 5.5.0.20241111 on Windows, obtained:

| Metric | Result |
| --- | --- |
| Exact expected status/state matches | 40/40 |
| Correct supported-state acceptances | 32/32 |
| False acceptances / operational errors | 0 / 0 |
| Precision among accepted samples / supported recall | 100% / 100% |
| Coverage (accepted / all samples) | 80% |

These are synthetic regression results, **not real-document accuracy**.
The report records image hashes, dimensions, dependency versions, settings,
timings and per-case results. Tesseract builds and language data are external
to the Python lock and can change the results.

### Evaluating real documents

Put authorized, labeled samples under the ignored `benchmarks/private/`
directory or outside the repository. Create a UTF-8 JSON manifest; image paths
are relative to its directory:

```json
[
  {"id": "pr-001", "image": "pr-001.jpg", "status": "recognized", "state": "PR"},
  {"id": "mg-001", "image": "mg-001.jpg", "status": "unsupported", "state": null},
  {"id": "unclear-001", "image": "unclear-001.png", "status": "unknown", "state": null}
]
```

```sh
python benchmark.py benchmarks/private/manifest.json --output benchmarks/local-real.json
python benchmark.py benchmarks/private/manifest.json --check
```

Each entry needs a unique nonempty ID, image path, expected status and state.
Only `recognized` entries have a state code; all abstentions use null.
Use pseudonymous IDs. Reports omit image paths and raw OCR, but include the
chosen IDs and image hashes; review a report before sharing it.

The runner counts unreadable images and OCR errors instead of dropping them.
It reports false acceptances, missed supported samples, precision, supported
recall, coverage and exact status/state matches. Undefined ratios are null.
`--check` requires every expected result to match, otherwise exits 1;
operational sample errors also exit 1 even without that flag. Setup errors exit 2.

Before claiming real-world accuracy, label the **issuing** state independently
of the software; include different document models, unsupported UFs, confusing
birthplaces, orientations and capture conditions. Separate development and held-out
evaluation by source document, keeping all variants of a document in the same
split. Record model/source/permission and acquisition conditions with the dataset.
A release candidate should have zero false acceptances on its reviewed regression
set; report recall and coverage as well so abstaining on everything cannot appear
successful. Representative real-data targets and model-specific acceptance criteria
remain to be established with the dataset owner.

### Repository, dependencies and development

| File | Purpose |
| --- | --- |
| [ludwig.py](ludwig.py) | Dataclasses, issuer rules, image/OCR pipeline, display and CLI |
| [benchmark.py](benchmark.py) | Synthetic generator and labeled-image evaluation CLI |
| [tests/](tests/) | Unit and regression tests |
| [benchmarks/text_cases.json](benchmarks/text_cases.json) | Labeled text corpus |
| [benchmarks/baseline.json](benchmarks/baseline.json) | Recorded synthetic OCR results |
| [requirements.txt](requirements.txt) | Direct dependency ranges |
| [requirements.lock](requirements.lock) | Exact direct and transitive Python versions |
| [.python-version](.python-version) | Python 3.15 target |
| [.github/workflows/ci.yml](.github/workflows/ci.yml) | Windows/Linux tests and Linux real-OCR regression |
| [LICENSE](LICENSE) | Full GNU GPL v3 text |
| [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) | Copyright, legacy MIT notice and reference-material scope |

NumPy handles arrays; OpenCV handles processing/display; Pillow validates
headers, decodes images and applies EXIF; pytesseract invokes Tesseract.
The lock pins NumPy 2.5.4, OpenCV 4.14.0.94, Pillow 13.0.0,
pytesseract 0.3.13 and packaging 26.3. It is a version lock, not an artifact-hash
lock or a pin of the operating system/Tesseract. Revalidate both test suites
when updating it.

GitHub Actions is configured to install the lock and run unit tests on Windows
and Linux with Python 3.15. A separate Linux job installs Tesseract/Portuguese data
and checks the synthetic corpus. The workflow has not been executed on GitHub
from this local workspace.

Importing modules has no application side effects:

```python
from ludwig import identify_issuer, identify_state

result = identify_issuer("ESTADO DO PARANÁ; NATURALIDADE: SÃO PAULO")
assert result.status == "recognized"
assert result.state.code == "PR"
assert identify_state("NATURALIDADE: SÃO PAULO") is None
```

To add a supported state, extend `STATES`, supply a documented/authorized
reference for desktop use, and add text, synthetic and real-document validation.
Keep unknown/ambiguous cases in the regression set.

### Troubleshooting and migration

| Symptom | Action |
| --- | --- |
| Missing `di.jpg` | Supply your own image path; quote paths containing spaces. |
| Missing Python module | Install the lock using the interpreter that runs the script. |
| Tesseract not found or missing `por` | Check `--tesseract-cmd`, `PATH` and `--list-langs`. |
| OCR timeout | Improve the input or set a known rotation; increase the shared budget if appropriate. |
| Unknown/low-confidence/ambiguous result | Inspect issuing fields, legibility and image orientation; do not infer the issuer from a birthplace. |
| Image limit or format error | Export a single JPEG/PNG within the documented bounds. |
| Missing reference or GUI failure | Keep the authorized reference beside the script, or use `--no-display`. |
| Waiting after recognition | Desktop mode waits for a key in an OpenCV window. |

The Python 2 migration replaced `xrange`, removed `imutils`, kept OCR as
Unicode, fixed the RS reference and added import-safe CLI/error handling.
This revision intentionally replaces global substring priority with issuer
context and abstention, replaces the 803-pixel resize with bounded preprocessing,
and removes the unconditional 90° display rotation/fixed crop. Existing callers
that supplied bare state names must now provide issuing context.

### License

Project code and project-authored documentation are licensed under the
**GNU General Public License, version 3 or any later version**
(`GPL-3.0-or-later`). See [LICENSE](LICENSE) for the complete terms.
Original copyright © 2019 Pedro Henrique P.; the historical MIT attribution
is retained in [LICENSES/MIT-legacy.txt](LICENSES/MIT-legacy.txt).

The inherited photographs and booklet are excluded from this GPL grant.
Their permissions are not established by changing the code license; see
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

---

<a id="portugues-brasileiro"></a>

## Português brasileiro

### Objetivo e escopo

O Ludwig é uma aplicação local em Python 3.15 para auxiliar a inspeção de imagens
de documentos de identidade brasileiros. Usa OCR em português para encontrar
**evidências do estado emissor**, informa incertezas e pode exibir o documento
orientado ao lado de uma referência histórica de perfuração para comparação manual.

Os estados de saída contemplados são **SC, SP, PR e RS**. As demais UFs brasileiras
são detectadas como emissores fora do escopo. É um protótipo educacional baseado
em regras: reconhecer o emissor não comprova autenticidade, e nenhuma geração
específica de RG ou modelo de CIN foi validado com uma base real representativa.

As entradas são imagens **JPEG ou PNG**, de quadro único, com contexto de emissão
legível. PDFs, captura de câmera, correção de perspectiva, segmentação automática
do documento e verificação automática de perfurações estão fora do escopo
implementado. A entrada padrão `di.jpg` não está incluída.

### Início rápido

Instale Python 3.15 e [Tesseract com dados do idioma português](https://tesseract-ocr.github.io/tessdoc/Installation.html).
Confira se `tesseract --list-langs` inclui `por`.
No diretório do projeto, use PowerShell no Windows:

```powershell
py -3.15 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock
.\.venv\Scripts\python.exe ludwig.py "caminho\para\documento.jpg" --no-display
```

No Linux/macOS, com Python 3.15 e Tesseract já instalados:

```sh
python3.15 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
.venv/bin/python ludwig.py "caminho/para/documento.jpg" --no-display
```

Se o inicializador do Windows não estiver disponível, substitua `py -3.15`
pelo caminho completo do executável Python 3.15. A ativação é opcional: os comandos
usam o ambiente virtual diretamente. `.python-version` registra a versão
pretendida, mas não instala o Python.

O Tesseract é instalado separadamente; o `pip` instala apenas a interface
Python. Consulte a [documentação dos dados de idioma](https://tesseract-ocr.github.io/tessdoc/Data-Files.html).
Para uma instalação no Windows fora do `PATH`:

```powershell
& "C:\Program Files\Tesseract-OCR\tesseract.exe" --list-langs
.\.venv\Scripts\python.exe ludwig.py "documento.jpg" --json --tesseract-cmd "C:\Program Files\Tesseract-OCR\tesseract.exe"
```

### Comandos e resultados

Os exemplos abaixo usam o `python` de um ambiente ativado; os caminhos
explícitos do interpretador apresentados acima também funcionam.

```sh
python ludwig.py documento.jpg --no-display
python ludwig.py documento.jpg --json
python ludwig.py documento.jpg --rotation 180 --ocr-timeout 45
python ludwig.py documento.jpg --crop "0.15,0.69,0.77,0.80"
python ludwig.py --help
```

| Argumento | Comportamento | Padrão |
| --- | --- | --- |
| `image` | Caminho JPEG/PNG, relativo ao diretório de trabalho | `di.jpg` |
| `--no-display` | Imprime resultados sem carregar referências nem abrir janelas | Desativado |
| `--json` | Emite resultado estruturado; implica execução sem janelas | Desativado |
| `--tesseract-cmd PATH` | Localização do executável Tesseract | Busca no `PATH` |
| `--rotation auto|0|90|180|270` | Correção no sentido horário após orientação EXIF | `auto` |
| `--ocr-timeout SECONDS` | Tempo de OCR compartilhado entre rotações; finito, maior que 0 e no máximo 300 | 30 |
| `--min-confidence SCORE` | Pontuação mínima da linha no Tesseract, de 0 a 100 | 60 |
| `--crop x0,y0,x1,y1` | Recorte manual de comparação na imagem orientada, em coordenadas normalizadas | Imagem inteira |

No modo gráfico, selecione uma janela do OpenCV e pressione uma tecla para
encerrar. O recorte afeta apenas a comparação visual; o OCR sempre examina a
imagem inteira. As referências são localizadas ao lado de `ludwig.py`,
independentemente do diretório de trabalho.

| Status | Significado |
| --- | --- |
| `recognized` | Um único candidato contemplado atende ao limite de confiança do OCR |
| `ambiguous` | Evidências conflitantes de emissores, inclusive entre rotações |
| `unsupported` | Um emissor encontrado fora de SC/SP/PR/RS |
| `low_confidence` | Um candidato encontrado, mas com pontuação insuficiente |
| `unknown` | Nenhuma evidência legível de emissão corresponde às regras |
| `error` | Falha de entrada, OCR, tempo limite ou exibição |

O código de saída **0** indica um emissor contemplado reconhecido e processamento
concluído; **1** indica abstenção do detector; **2** indica erro operacional ou de
argumentos. A ajuda também retorna 0. As mensagens são em português e a ajuda da
linha de comando é em inglês. Erros normais vão para stderr. O modo JSON emite
um único objeto em stdout, inclusive para erros operacionais; erros de uso do
argparse continuam em stderr.

O JSON contém `status`, `state` (objeto com código/nome/descrição, ou null),
`candidates`, `confidence`, `rotation` e `evidence`.
As evidências contêm apenas UFs, pontuações e rotações. Não incluem texto bruto
de OCR, nomes, números de documentos ou pixels. Objetos de erro operacional
contêm, em vez disso, `status: "error"` e `error`.

### Regras de reconhecimento e processamento

1. Leia no máximo **20 MiB** de dados codificados, mais um byte para detectar
   excesso. Inspecione os cabeçalhos antes de decodificar pixels: cada dimensão
   deve ter entre 32 e 12.000 pixels, com no máximo **20 milhões de pixels** no
   total. Rejeite animações, múltiplos quadros e formatos não contemplados.
2. Aplique a orientação EXIF e componha transparências sobre branco. Converta
   para tons de cinza; preserve a proporção, limite o maior lado do OCR a
   2.000 pixels e amplie no máximo 2×.
3. Execute Tesseract em português (`por`) com `--psm 6`. O modo automático
   verifica 0°, 90°, 180° e 270° com um prazo compartilhado. Todas as tentativas
   devem terminar; um timeout não retorna um sucesso parcial anterior. O prazo
   abrange o pré-processamento e as chamadas de OCR, não a leitura do arquivo
   nem a exibição interativa.
4. Normalize acentos, maiúsculas e espaços. Exija um cabeçalho como
   `ESTADO DE`, `ÓRGÃO EXPEDIDOR`, `UF DE EMISSÃO` ou de um órgão
   reconhecido. Busque nomes completos ou siglas de UF nesse contexto. Um
   cabeçalho sem valor pode usar a linha imediatamente seguinte no mesmo
   parágrafo do OCR. Rótulos conhecidos de naturalidade, endereço, nome e outros
   campos pessoais encerram o contexto de emissão.
5. Reúna candidatos entre rotações. Candidatos conflitantes levam à abstenção,
   mesmo se um tiver pontuação menor. Para um único candidato, use sua maior
   pontuação observada e a orientação correspondente; aceite somente se atingir
   o limite configurado.

Exemplos cobertos pelos testes:

| Texto do OCR | Resultado |
| --- | --- |
| `ESTADO DO PARANÁ; NATURALIDADE: SANTA CATARINA` | PR |
| `ESTADO DE MINAS GERAIS; NATURALIDADE: SÃO PAULO` | Emissor fora do escopo |
| `ÓRGÃO EXPEDIDOR: SSP/SP` | SP |
| `ESTADO DO PARANÁ` e `ESTADO DE SANTA CATARINA` | Ambíguo |
| `PARANAPANEMA`, `SÃO PAULO` isolado ou apenas naturalidade | Desconhecido |

A pontuação é a menor confiança de token na linha do OCR, incluindo outros
tokens da mesma linha. Cabeçalhos divididos mantêm a menor pontuação das linhas.
Ela **não é uma probabilidade calibrada de acerto do emissor**. Chamadas da API
com texto não têm pontuação de OCR e informam confiança null. Nomes com erros,
como `SA0 PAULO`, não são corrigidos silenciosamente. Rótulos ausentes,
formatos incomuns, inclinação, sombras e erros na ordem dos campos ainda podem
provocar rejeições ou classificações incorretas.

### Estados e referências de comparação

| Estado | UF | Descrições históricas impressas | Referência |
| --- | --- | --- | --- |
| Santa Catarina | SC | SSPSC, IGP-SC, SSP, IISC | [SC.jpg](SC.jpg) |
| São Paulo | SP | IIRGD | [SP.jpg](SP.jpg) |
| Paraná | PR | SESP, IIPR | [PR.jpg](PR.jpg) |
| Rio Grande do Sul | RS | IIRS, DIRS | [RS.jpg](RS.jpg) |

As descrições dos órgãos foram herdadas como referência; não constituem um
diretório atual verificado nem indicam que toda sigla se aplica a qualquer
modelo. As quatro fotografias pequenas são ilustrativas; não há pontuação
automática de similaridade nem recorte universal de perfuração. A exibição
padrão mostra o documento orientado inteiro. O recorte manual exige
`0 <= x0 < x1 <= 1` e `0 <= y0 < y1 <= 1`, com pelo menos 2 pixels
em cada dimensão.

A [apostila de 2019](<Apostila/Documentoscopia - Na Verificação Rápida de Documentos (2019).pdf>)
é material complementar e nunca é carregada pelo programa. Procedência,
permissões e aplicação aos modelos estão registradas em
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md); permanecem não verificadas
quando o repositório original não fornece comprovação.

### Testes e resultado de referência

```sh
python -m unittest discover -s tests -v
python -m pip check
python benchmark.py --synthetic --check --output benchmarks/local-synthetic.json
```

Os testes exercitam classificação, limites antes da decodificação, EXIF,
rotação, recorte, prazos de OCR, tratamento de erros, JSON, escolha de referências
e métricas do benchmark. Simulam OCR/janelas e dispensam executável Tesseract ou
ambiente gráfico. Os [32 casos de texto rotulados](benchmarks/text_cases.json)
incluem estados contemplados, outros emissores, ambiguidades, naturalidade
isolada e texto danificado.

O benchmark separado executa **Tesseract real** em **40 imagens geradas**:
32 exemplos contemplados (quatro estados × orientação original/90°/180°/270°/
desfoque/margens/baixo contraste/artefatos JPEG) e oito casos desconhecidos,
ambíguos ou fora do escopo. Contêm texto inventado, sem identidades pessoais
nem digitalizações da apostila.

O [resultado registrado](benchmarks/baseline.json), com Python 3.15.0 e
Tesseract 5.5.0.20241111 no Windows, apresentou:

| Métrica | Resultado |
| --- | --- |
| Correspondências exatas de status/estado esperado | 40/40 |
| Aceitações corretas de estados contemplados | 32/32 |
| Aceitações incorretas / erros operacionais | 0 / 0 |
| Precisão entre aceitos / sensibilidade para estados contemplados | 100% / 100% |
| Cobertura (aceitos / total) | 80% |

São resultados de regressão sintética, **não precisão em documentos reais**.
O relatório registra hashes e dimensões das imagens, versões de dependências,
configuração, tempos e resultados por caso. Versões do Tesseract e dados de idioma
são externos ao lock Python e podem alterar os resultados.

### Avaliação com documentos reais

Coloque amostras autorizadas e rotuladas no diretório ignorado
`benchmarks/private/` ou fora do repositório. Crie um manifesto JSON UTF-8;
os caminhos das imagens são relativos ao diretório dele:

```json
[
  {"id": "pr-001", "image": "pr-001.jpg", "status": "recognized", "state": "PR"},
  {"id": "mg-001", "image": "mg-001.jpg", "status": "unsupported", "state": null},
  {"id": "unclear-001", "image": "unclear-001.png", "status": "unknown", "state": null}
]
```

```sh
python benchmark.py benchmarks/private/manifest.json --output benchmarks/local-real.json
python benchmark.py benchmarks/private/manifest.json --check
```

Cada item exige ID único não vazio, imagem, status esperado e estado.
Somente itens `recognized` têm código de UF; abstenções usam null.
Use IDs sem dados identificadores. Relatórios omitem caminhos e OCR bruto,
mas incluem os IDs escolhidos e hashes das imagens; revise antes de compartilhar.

O avaliador conta imagens ilegíveis e erros de OCR, sem excluí-los da medição.
Informa aceitações incorretas, amostras contempladas não identificadas
corretamente, precisão, sensibilidade, cobertura e correspondências exatas de
status/estado. Razões indefinidas são null. `--check` exige correspondência
de todos os resultados; caso contrário, retorna 1. Erros operacionais nas amostras
também retornam 1 sem essa opção. Erros de configuração retornam 2.

Antes de declarar precisão prática, rotule o estado **emissor** independentemente
do programa; inclua diferentes modelos, UFs fora do escopo, naturalidades
confundíveis, orientações e condições de captura. Separe desenvolvimento e
avaliação independente por documento de origem, mantendo suas variantes na
mesma divisão. Registre modelo, fonte, permissão e condições de aquisição
junto à base. Uma versão candidata deve ter zero aceitações incorretas no
conjunto de regressão revisado; informe também sensibilidade e cobertura para
que rejeitar tudo não pareça sucesso. Metas representativas com dados reais e
critérios por modelo ainda precisam ser estabelecidos com o responsável pela base.

### Repositório, dependências e desenvolvimento

| Arquivo | Função |
| --- | --- |
| [ludwig.py](ludwig.py) | Dataclasses, regras de emissão, imagens/OCR, exibição e CLI |
| [benchmark.py](benchmark.py) | Gerador sintético e CLI de avaliação de imagens rotuladas |
| [tests/](tests/) | Testes unitários e de regressão |
| [benchmarks/text_cases.json](benchmarks/text_cases.json) | Base de texto rotulada |
| [benchmarks/baseline.json](benchmarks/baseline.json) | Resultados registrados de OCR sintético |
| [requirements.txt](requirements.txt) | Faixas das dependências diretas |
| [requirements.lock](requirements.lock) | Versões exatas das dependências Python diretas e transitivas |
| [.python-version](.python-version) | Versão-alvo Python 3.15 |
| [.github/workflows/ci.yml](.github/workflows/ci.yml) | Testes Windows/Linux e regressão de OCR real no Linux |
| [LICENSE](LICENSE) | Texto integral da GNU GPL v3 |
| [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) | Copyright, aviso MIT histórico e escopo dos materiais |

NumPy trabalha com arrays; OpenCV processa e exibe; Pillow valida cabeçalhos,
decodifica e aplica EXIF; pytesseract invoca o Tesseract.
O lock fixa NumPy 2.5.4, OpenCV 4.14.0.94, Pillow 13.0.0,
pytesseract 0.3.13 e packaging 26.3. Ele fixa versões, sem hashes de artefatos
nem fixação do sistema operacional/Tesseract. Revalide testes e benchmark
ao atualizá-lo.

O GitHub Actions está configurado para instalar o lock e executar testes no
Windows e Linux com Python 3.15. Um trabalho Linux separado instala Tesseract e
dados de português e verifica a base sintética. O workflow não foi executado
no GitHub a partir desta cópia de trabalho local.

Importar os módulos não executa a aplicação:

```python
from ludwig import identify_issuer, identify_state

result = identify_issuer("ESTADO DO PARANÁ; NATURALIDADE: SÃO PAULO")
assert result.status == "recognized"
assert result.state.code == "PR"
assert identify_state("NATURALIDADE: SÃO PAULO") is None
```

Para incluir um estado, amplie `STATES`, forneça uma referência documentada
e autorizada para o modo gráfico e acrescente validação textual, sintética e com
documentos reais. Mantenha casos desconhecidos/ambíguos na regressão.

### Solução de problemas e migração

| Sintoma | Ação |
| --- | --- |
| `di.jpg` ausente | Informe sua imagem; use aspas em caminhos com espaços. |
| Módulo Python ausente | Instale o lock com o interpretador usado para executar o script. |
| Tesseract não encontrado ou idioma `por` ausente | Confira `--tesseract-cmd`, `PATH` e `--list-langs`. |
| Tempo de OCR excedido | Melhore a entrada ou informe uma rotação conhecida; amplie o prazo se adequado. |
| Resultado desconhecido, incerto ou ambíguo | Confira emissão, legibilidade e orientação; não deduza o emissor pela naturalidade. |
| Limite de imagem ou formato inválido | Exporte um JPEG/PNG único dentro dos limites documentados. |
| Referência ausente ou falha gráfica | Mantenha a referência autorizada ao lado do script ou use `--no-display`. |
| Espera após reconhecimento | O modo gráfico aguarda uma tecla em uma janela do OpenCV. |

A migração do Python 2 substituiu `xrange`, removeu `imutils`, manteve
OCR como Unicode, corrigiu a referência RS e adicionou CLI segura na importação
e tratamento de erros. Esta revisão substitui a prioridade de substrings por
contexto de emissão e abstenção, troca o redimensionamento de 803 pixels pelo
processamento limitado e remove rotação incondicional de 90°/recorte fixo da
exibição. Chamadores que forneciam apenas nomes de estados precisam agora
incluir contexto de emissão.

### Licença

Código e documentação de autoria do projeto são licenciados sob a
**GNU General Public License, versão 3 ou qualquer versão posterior**
(`GPL-3.0-or-later`). Consulte [LICENSE](LICENSE) para os termos completos.
Copyright original © 2019 Pedro Henrique P.; a atribuição MIT histórica foi
preservada em [LICENSES/MIT-legacy.txt](LICENSES/MIT-legacy.txt).

Fotografias herdadas e apostila estão excluídas dessa concessão GPL.
A mudança de licença do código não estabelece suas permissões; consulte
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
