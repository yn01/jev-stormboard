"""Jev に投げる質問の定義。

質問は QUESTIONS のリストに並べる。**要素を足すだけで質問数が増える**ので、
10問から60問へ広げるときも、このファイルに追記するだけでよい(requirements.md R-22)。

各質問には観点のタグ(aspect)を持たせる。将来、観点ごとに絞り込んだり、画面で
グループ表示したりするために使う(R-23)。

全問は1回の Jev リクエストにまとめて送る(R-24)。質問ごとに個別のリクエストは送らない。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from typesafe_sdk import Choice, Noul, Score

# 観点のタグ。増やしてよい
ASPECT_MESSAGE = "message"  # 電文そのものの読み取り
ASPECT_PLAN = "plan"  # 予定をどうするか
ASPECT_PREPARE = "prepare"  # 備え
ASPECT_MOVE = "move"  # 移動・外出
ASPECT_HOME = "home"  # 住まい
ASPECT_FAMILY = "family"  # 家族・周囲
ASPECT_ACTION = "action"  # 取るべき行動のまとめ


@dataclass(frozen=True)
class Q:
    """質問1問。

    key      : 結果を引くときの名前(Jev へ渡す質問名と同じ)
    label    : 画面や表に出す短い日本語
    aspect   : 観点のタグ
    question : Jev の質問オブジェクト(Choice / Score / Noul)
    """

    key: str
    label: str
    aspect: str
    question: Choice | Score | Noul
    tags: tuple[str, ...] = field(default=())


# 電文とプロファイルの読み方は、全問に共通する前提として各質問の文面に書く。
_STATE_NOTE = (
    "`message` は気象庁が発表した防災電文である。`person` はこの電文を受け取る人物の情報で、"
    "`person.profile` にその人の暮らしや事情が書かれている。"
)


QUESTIONS: list[Q] = [
    # ------------------------------------------------ 電文そのものへの質問(3問)
    Q(
        key="message_kind",
        label="電文の位置づけ",
        aspect=ASPECT_MESSAGE,
        question=Choice(
            instructions=(
                f"{_STATE_NOTE} この電文が、発表の流れの中でどの位置づけにあるかを1つ選べ。"
                "`message.info_type`(発表・訂正・取消)や、`message.warnings` の各 status"
                "(発表・継続・解除)、見出しの書きぶりを手がかりにすること。"
            ),
            criteria={
                "新規発表": "これまで出ていなかった警報・注意報や情報が、新たに発表された。",
                "切替・強化": (
                    "すでに出ていたものが、より重い段階に切り替わった"
                    "(注意報から警報へ、警報から特別警報へなど)。"
                ),
                "継続": "すでに出ている警報・注意報や情報が、内容を変えずに継続している。",
                "解除": "出ていた警報・注意報が解除された、または情報の発表が終了した。",
                "参考情報": (
                    "警報・注意報の発表や解除ではなく、今後の見通しや解説を伝える情報"
                    "(気象情報、早期注意情報、週間予報など)。"
                ),
            },
        ),
    ),
    Q(
        key="imminent",
        label="切迫している",
        aspect=ASPECT_MESSAGE,
        question=Noul(
            instructions=(
                f"{_STATE_NOTE} この電文は、数時間以内に災害が発生する切迫した状況を述べているか。"
                "「ただちに」「これから」「夜遅くにかけて」など時間が近いこと、"
                "「厳重に警戒」「命を守る行動」など強い表現があることを手がかりにすること。"
                "数日先の見通しを述べているだけなら偽。"
            ),
        ),
    ),
    Q(
        key="severity",
        label="事象の深刻さ",
        aspect=ASPECT_MESSAGE,
        question=Score(
            instructions=(
                f"{_STATE_NOTE} この電文が示す事象の深刻さを、人物の事情とは切り離して、"
                "電文そのものの内容から評価せよ。"
            ),
            criteria=[
                "深刻さはない。予報や概況の伝達にとどまる。",
                "軽微。注意報級で、日常生活への支障はほとんどない。",
                "中程度。注意報が複数出ている、または警報が出る可能性が示されている。",
                "重大。警報が発表され、災害が発生するおそれがある。",
                "非常に重大。特別警報級、または重大な災害の発生が切迫している。",
            ],
        ),
    ),
    # ------------------------------------------------ この人にとっての質問(7問)
    Q(
        key="relevant",
        label="この地域に関係する",
        aspect=ASPECT_MESSAGE,
        question=Noul(
            instructions=(
                f"{_STATE_NOTE} この情報は、この人が住む地域に関係するか。"
                "`person.pref` / `person.city` / `person.area_code` と、"
                "`message.areas`(電文が対象とする地域とコード)を突き合わせて判断せよ。"
                "市区町村が一致する場合はもちろん、同じ都道府県内の一次細分区域"
                "(東京地方など)に含まれる場合も真とする。"
                "離島や他県だけが対象で、この人の住む地域が含まれないなら偽。"
                "通勤先や家族の住む場所が対象に含まれる場合も、関係するとみなしてよい。"
            ),
        ),
    ),
    Q(
        key="change_plan",
        label="予定の変更",
        aspect=ASPECT_PLAN,
        question=Noul(
            instructions=(
                f"{_STATE_NOTE} この人は、予定を中止または延期すべき状況か。"
                "`person.profile` に書かれた外出や旅行、通勤の予定と、電文が示す"
                "時間帯・地域・強さを照らし合わせて判断せよ。"
            ),
        ),
    ),
    Q(
        key="prepare_now",
        label="今すぐ備える",
        aspect=ASPECT_PREPARE,
        question=Noul(
            instructions=(
                f"{_STATE_NOTE} この人は、今のうちに買い出しや備え(水・食料・停電への備え・"
                "窓や庭まわりの片付けなど)を済ませておくべき状況か。"
                "`person.profile` に書かれた備蓄の状況も踏まえること。"
                "事象までまだ時間があり、かつ影響が見込まれる場合ほど真に近い。"
            ),
        ),
    ),
    Q(
        key="stay_home",
        label="外出を控える",
        aspect=ASPECT_MOVE,
        question=Noul(
            instructions=(
                f"{_STATE_NOTE} この人は、不要な外出を控えるべき状況か。"
                "電文が示す時間帯に、この人が屋外にいることや移動することが"
                "危険または困難になるかどうかで判断せよ。"
            ),
        ),
    ),
    Q(
        key="contact_family",
        label="家族への連絡",
        aspect=ASPECT_FAMILY,
        question=Noul(
            instructions=(
                f"{_STATE_NOTE} この人は、離れて暮らす家族に連絡や声かけをすべき状況か。"
                "`person.profile` に離れて暮らす家族(高齢の親など)が書かれており、"
                "その人たちが住む地域が電文の対象に含まれる、または影響を受けると"
                "見込まれる場合に真に近くなる。"
            ),
        ),
    ),
    Q(
        key="impact",
        label="生活への影響度",
        aspect=ASPECT_HOME,
        question=Score(
            instructions=(
                f"{_STATE_NOTE} この電文が示す事象による、この人の生活への影響度を評価せよ。"
                "住まい、通勤、子どもの学校、買い物、家族への影響を考えること。"
                "電文そのものの深刻さではなく、**この人にとっての**影響で評価する。"
            ),
            criteria=[
                "影響はない。いつもどおり過ごせる。",
                "わずかな影響。傘や雨具が必要になる程度。",
                "ある程度の影響。交通の乱れや、予定の調整が必要になるかもしれない。",
                "大きな影響。通勤や学校、外出の予定を変える必要がある。",
                "非常に大きな影響。在宅か避難かの判断や、住まいの安全の確保が必要になる。",
            ],
        ),
    ),
    Q(
        key="action",
        label="取るべき行動",
        aspect=ASPECT_ACTION,
        question=Choice(
            instructions=(
                f"{_STATE_NOTE} この人がいま取るべき行動として、最も適切なものを1つ選べ。"
                "電文の内容と `person.profile` の事情の両方を踏まえること。"
            ),
            criteria={
                "通常どおり": "特別な対応は要らない。いつもどおり過ごしてよい。",
                "予定変更を検討": "外出や移動の予定を、中止・延期・時間変更するか検討する段階。",
                "今日中に備える": "買い出し、備蓄の確認、停電への備え、屋外の片付けを済ませておく段階。",
                "外出を控える": "不要な外出をやめ、屋内で安全に過ごす段階。",
                "早めの避難を検討": (
                    "自宅にとどまることが危険になりうるため、"
                    "明るいうちの避難や、安全な場所への移動を検討する段階。"
                ),
            },
        ),
    ),
]


def build_questions() -> dict[str, Choice | Score | Noul]:
    """QUESTIONS から、system_one に渡す辞書を組み立てる。"""
    return {q.key: q.question for q in QUESTIONS}


def by_key(key: str) -> Q:
    for q in QUESTIONS:
        if q.key == key:
            return q
    raise KeyError(key)


def aspects() -> list[str]:
    """定義されている観点の一覧(定義順、重複なし)。"""
    seen: list[str] = []
    for q in QUESTIONS:
        if q.aspect not in seen:
            seen.append(q.aspect)
    return seen
