# Data Annotator

이미지 결함 검사용 **점(point) 기반 어노테이션 도구**입니다. 클래스/속성 조합마다 색깔 점을 찍어 결함 위치를 표시하고, 검사 모델 학습에 바로 쓰는 `annotations.json` 형식으로 저장합니다.

![icon](icon.png)

---

## 1. 빠른 시작

### 미리 빌드된 exe로 실행 (가장 쉬움)

```
dist\DataAnnotator.exe
```
더블클릭하면 끝. Python 설치 불필요.

### 소스에서 실행

```powershell
.venv\Scripts\python main.py
```
또는 직접 환경 구성:
```powershell
python -m venv .venv
.venv\Scripts\python -m pip install --disable-pip-version-check pyqt5 pillow
.venv\Scripts\python main.py
```

> **주의** — anaconda Python으로 PyInstaller 빌드 시 Qt5 DLL 리네임 문제(`Qt5Core_conda.dll`)로 exe가 실행되지 않습니다. 빌드할 때는 반드시 위 venv처럼 **pip로 설치한 PyQt5**를 사용하세요.

---

## 2. 기본 작업 흐름

```
[Open Folder] → 이미지 선택 → 클래스/속성 선택 → 이미지 좌클릭으로 점 찍기 → [Save]
```

1. **`Open Folder`** (Ctrl+O) — 데이터셋 폴더 열기. 하위 폴더 재귀 탐색해서 모든 이미지를 좌측 리스트에 표시합니다.
2. 좌측 리스트에서 이미지 선택. 이미 어노테이션된 이미지는 녹색 + `[N]` 카운트로 표시됩니다.
3. 우측 **Active label**에서 클래스/속성 선택 (또는 직접 입력하면 스키마에 자동 추가).
4. 이미지 위 **좌클릭** → 점 추가. 같은 (클래스, 속성) 조합으로 여러 점을 찍을 수 있습니다.
5. **`Save`** (Ctrl+S) — 데이터셋 루트에 `annotations.json` 저장.

이미 작성된 어노테이션 파일이 있다면 **`Load Annotations`** (Ctrl+L)로 불러와서 이어 작업할 수 있습니다.

---

## 3. 단축키 / 마우스 조작

### 메뉴

| 단축키 | 동작 |
|---|---|
| `Ctrl+O` | 폴더 열기 |
| `Ctrl+Shift+O` | 단일 이미지 열기 |
| `Ctrl+L` | annotations.json 불러오기 |
| `Ctrl+S` | 저장 |
| `Ctrl+Shift+S` | 다른 이름으로 저장 |
| `A` / `D` | 이전 / 다음 이미지 |
| `F` | 화면 맞춤 (Fit) |
| `Delete` | 선택된 점 또는 항목 삭제 |
| `+` / `-` | 확대 / 축소 |

### 캔버스 마우스

| 입력 | 동작 |
|---|---|
| **좌클릭** (빈 영역) | 현재 라벨로 점 추가 |
| **좌클릭** (점) | 점 선택 |
| **좌클릭 + 드래그** (점) | 점 이동 |
| **우클릭** (점) | 해당 점 삭제 |
| **휠** | 마우스 위치 기준 확대/축소 |
| **휠 클릭 + 드래그** | 화면 이동(pan) |
| **Ctrl + 좌클릭 드래그** | 화면 이동 |
| **Space + 좌클릭 드래그** | 화면 이동 |

---

## 4. UI 패널 구성

```
┌──────────┬────────────────────────────────┬──────────────┐
│  Images  │           Canvas               │ Active label │
│  (filter)│        (이미지 + 점 표시)       │ Annotations  │
│          │                                │   Legend     │
└──────────┴────────────────────────────────┴──────────────┘
```

### 좌측 — Images
- 필터 입력으로 파일명 부분일치 검색.
- 어노테이션이 있는 이미지는 `이미지경로    [점 개수]` 형태로 카운트 표시 + 녹색 강조.

### 중앙 — Canvas
- 이미지가 표시되는 작업 영역.
- 색깔 점은 화면 픽셀 기준 항상 같은 크기로 그려지므로 줌 레벨과 무관하게 잘 보입니다.
- 점 우측에 `{속성} {클래스}` (또는 Prompt 오버라이드) 라벨 알약(pill)이 표시됩니다.

### 우측 — Active label
- **Class / Attribute** 드롭다운: 미리 정의된 12개 클래스 + 18개 속성 (아래 [기본 스키마](#6-기본-스키마) 참조). 새 값을 타이핑하면 즉시 스키마에 추가됩니다.
- **Prompt 입력란**: 기본은 `"{속성} {클래스}"` (예: `"normal blue cuboid potentiometer"`). 다르게 쓰고 싶으면 직접 수정하면 됩니다 — 그 (클래스, 속성) 조합의 JSON 키가 바뀝니다. `↺` 버튼으로 기본값 복원.

### 우측 — Annotations on this image
- 현재 이미지의 (속성+클래스) 그룹별 점 개수 목록.
- 항목 선택 시 캔버스에서 해당 점들이 함께 선택 표시됩니다.
- `Remove entry` — 선택한 그룹 통째 삭제. `Clear image` — 현재 이미지의 모든 어노테이션 삭제.

### 우측 — Attribute legend
- 색깔 swatch + 속성명 범례. `class → attribute → color` 매핑을 한눈에 확인.

---

## 5. annotations.json 스키마

저장 형식은 다음과 같습니다 (이미지별 `{rel_path: {entry_key: {...}}}` 중첩).

```json
{
    "black/A_black_001.jpg": {
        "normal blue cuboid potentiometer": {
            "class": "blue cuboid potentiometer",
            "attribute": "normal",
            "points": [
                [1899.16, 1609.49],
                [1596.43, 773.17],
                [993.14, 777.52]
            ],
            "type": "inspection"
        }
    }
}
```

- **최상위 키** — 데이터셋 루트 기준 **상대경로** (예: `black/A_black_001.jpg`). 단일 이미지 모드는 파일명만.
- **entry_key** — 기본 `"{attribute} {class}"`. Prompt 입력란으로 자유롭게 변경 가능.
- **class / attribute** — 라벨 메타데이터.
- **points** — `[[x, y], ...]` 이미지 픽셀 좌표 (소수점 둘째 자리).
- **type** — 항상 `"inspection"`.

### Load 동작
- 불러오기 시 entry_key가 기본 형식이 아니면 자동으로 **Prompt 오버라이드로 기억**합니다 → 같은 (클래스, 속성)에 점을 추가해도 사용자가 정한 키가 유지됩니다.
- JSON에 등장한 클래스/속성은 자동으로 스키마에 등록됩니다.

---

## 6. 기본 스키마

| 클래스 | 속성 |
|---|---|
| `blue cuboid potentiometer` | normal, cracked, broken, bent lead |
| `bolt` | normal, contaminated, curved, scratched |
| `capacitor` | normal, peeled, ruptured, truncated, without terminals |
| `cylindrical capacitor` | normal, gouged, scratched |
| `fuse` | normal, broken, crushed, damaged terminal |
| `gear` | normal, broken, worn |
| `nut` | normal, contaminated |
| `plastic cap` | normal, broken, cracked, peeled |
| `plastic part` | normal, bent head |
| `plastic tube` | normal, shrunk, torn |
| `switch connector` | normal, bent lead, crushed |
| `washer` | normal, contaminated, curved, scratched |

색상은 속성 단위로 고정 (예: `normal` 녹색, `cracked` 빨강, `bent lead` 주황). 새로 등록한 속성은 해시 기반 폴백 팔레트에서 색이 자동 배정됩니다.

---

## 7. 빌드 (단일 exe 생성)

```powershell
.venv\Scripts\python -m PyInstaller --clean --noconfirm DataAnnotator.spec
```

- 결과물: `dist\DataAnnotator.exe` (단일 파일, ~35MB, 아이콘 임베드, 콘솔창 없음).
- `DataAnnotator.spec`이 onefile + 윈도우 + icon 번들을 모두 처리합니다.
- 아이콘 디자인 변경은 [`_gen_icon.py`](_gen_icon.py)의 `DOTS`/`BG_*` 수정 후 재실행 → `icon.ico`/`icon.png` 갱신 → 다시 빌드.

---

## 8. 자주 묻는 상황

- **저장은 어디로?** — 폴더 모드는 데이터셋 루트의 `annotations.json`. 단일 이미지 모드는 이미지 옆.
- **이전 작업 이어서 하려면?** — 폴더 열기 후 `Ctrl+L`로 기존 `annotations.json` 로드.
- **클래스가 목록에 없는데?** — Class/Attribute 드롭다운에 그냥 타이핑하세요. 점을 한 번 찍는 순간 스키마에 영구 등록됩니다.
- **점을 옮기다 이미지 밖으로 나갔어요** — 자동으로 이미지 경계 내로 clamp 됩니다.
- **닫을 때 안 저장하고 닫혔어요** — 더티 상태면 종료 시 저장 확인 다이얼로그가 뜹니다. Cancel로 종료를 막을 수 있습니다.
