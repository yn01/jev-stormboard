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
ASPECT_WORK = "work"  # 仕事(出社・在宅)
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
    # ================================================================
    # ここから先は、質問数の切り替え(10問 / 30問 / 50問)で使う追加分。
    # QUESTIONS の**並び順**がそのまま出題順になるので、先頭10問は変えないこと。
    # ================================================================

    # ------------------------------------------------ 移動(11〜18問目)
    Q(
        key="train_risk",
        label="電車の乱れ",
        aspect=ASPECT_MOVE,
        question=Noul(
            instructions=(
                f"{_STATE_NOTE} この人が使う鉄道に、運転見合わせや大幅な遅れが出る可能性が高いか。"
                "強風・大雨・高波は運転規制の理由になる。`person.profile` の通勤経路を踏まえること。"
            ),
        ),
    ),
    Q(
        key="train_early_return",
        label="早めの帰宅",
        aspect=ASPECT_MOVE,
        question=Noul(
            instructions=(
                f"{_STATE_NOTE} 外出中なら、交通が止まる前に早めに帰宅を始めるべき状況か。"
                "計画運休や運転見合わせが見込まれる時間帯かどうかで判断せよ。"
            ),
        ),
    ),
    Q(
        key="car_risk",
        label="車の運転",
        aspect=ASPECT_MOVE,
        question=Noul(
            instructions=(
                f"{_STATE_NOTE} 車の運転を避けるべき状況か。冠水、横風、視界不良、"
                "落下物などの危険があるかどうかで判断せよ。"
            ),
        ),
    ),
    Q(
        key="bicycle_risk",
        label="自転車",
        aspect=ASPECT_MOVE,
        question=Noul(
            instructions=(
                f"{_STATE_NOTE} 自転車での移動が危険または困難になる状況か。"
                "強風や雨は自転車に影響しやすいことを踏まえること。"
            ),
        ),
    ),
    Q(
        key="walk_risk",
        label="徒歩",
        aspect=ASPECT_MOVE,
        question=Noul(
            instructions=(
                f"{_STATE_NOTE} 徒歩での外出が危険になる状況か。飛散物、冠水した道路、"
                "強風での転倒、傘が使えないほどの風などを考えること。"
            ),
        ),
    ),
    Q(
        key="road_flood",
        label="道路の冠水",
        aspect=ASPECT_MOVE,
        question=Noul(
            instructions=(
                f"{_STATE_NOTE} この人の生活圏で道路が冠水するおそれがあるか。"
                "`person.profile` に書かれた地形や過去の冠水の経験も手がかりにすること。"
            ),
        ),
    ),
    Q(
        key="travel_cancel",
        label="遠出の中止",
        aspect=ASPECT_MOVE,
        question=Noul(
            instructions=(
                f"{_STATE_NOTE} 日帰りや泊まりの遠出を中止すべき状況か。"
                "`person.profile` に予定が書かれていれば、その日時と照らし合わせること。"
            ),
        ),
    ),
    Q(
        key="move_difficulty",
        label="移動の困難さ",
        aspect=ASPECT_MOVE,
        question=Score(
            instructions=(
                f"{_STATE_NOTE} この人にとって、移動(通勤・買い物・送り迎え)がどれだけ"
                "困難になるかを評価せよ。"
            ),
            criteria=[
                "支障はない。いつもどおり移動できる。",
                "わずかな支障。雨具が必要な程度。",
                "ある程度の支障。遅れや迂回が必要になる。",
                "大きな支障。移動手段を変えるか、時間をずらす必要がある。",
                "移動は避けるべき。外に出ること自体が危険。",
            ],
        ),
    ),
    # ------------------------------------------------ 備え(19〜26問目)
    Q(
        key="shopping_now",
        label="買い出し",
        aspect=ASPECT_PREPARE,
        question=Noul(
            instructions=(
                f"{_STATE_NOTE} 事象が来る前に、水・食料の買い出しを済ませておくべき状況か。"
                "`person.profile` の備蓄の状況と、事象までの残り時間を踏まえること。"
            ),
        ),
    ),
    Q(
        key="charge_devices",
        label="充電",
        aspect=ASPECT_PREPARE,
        question=Noul(
            instructions=(
                f"{_STATE_NOTE} スマートフォンやモバイルバッテリーを充電しておくべき状況か。"
                "停電のおそれがあるかどうかが手がかりになる。"
            ),
        ),
    ),
    Q(
        key="balcony_tidy",
        label="ベランダの片付け",
        aspect=ASPECT_PREPARE,
        question=Noul(
            instructions=(
                f"{_STATE_NOTE} ベランダや庭、玄関まわりの物を室内へ入れる、"
                "または固定すべき状況か。強風で飛ばされる危険があるかで判断せよ。"
            ),
        ),
    ),
    Q(
        key="flood_barrier",
        label="浸水対策",
        aspect=ASPECT_PREPARE,
        question=Noul(
            instructions=(
                f"{_STATE_NOTE} 土のうや止水板、家財の上階への移動といった浸水対策を"
                "しておくべき状況か。`person.profile` の住まいと浸水の想定を踏まえること。"
            ),
        ),
    ),
    Q(
        key="water_storage",
        label="水の確保",
        aspect=ASPECT_PREPARE,
        question=Noul(
            instructions=(
                f"{_STATE_NOTE} 断水に備えて、飲料水や生活用水(浴槽に水を張るなど)を"
                "確保しておくべき状況か。"
            ),
        ),
    ),
    Q(
        key="cash_medicine",
        label="現金・常備薬",
        aspect=ASPECT_PREPARE,
        question=Noul(
            instructions=(
                f"{_STATE_NOTE} 停電でカードが使えない場合に備えた現金や、"
                "常備薬の残量を確認しておくべき状況か。"
            ),
        ),
    ),
    Q(
        key="hazard_check",
        label="避難先の確認",
        aspect=ASPECT_PREPARE,
        question=Noul(
            instructions=(
                f"{_STATE_NOTE} ハザードマップや避難所の場所を、いま確認しておくべき状況か。"
                "災害が起きてからでは遅い段階かどうかで判断せよ。"
            ),
        ),
    ),
    Q(
        key="prepare_urgency",
        label="備えの緊急度",
        aspect=ASPECT_PREPARE,
        question=Score(
            instructions=(
                f"{_STATE_NOTE} この人が備えを進めるべき緊急度を評価せよ。"
                "事象までの残り時間と、見込まれる影響の両方を考えること。"
            ),
            criteria=[
                "備えは要らない。",
                "気に留めておく程度でよい。",
                "数日のうちに備えておくとよい。",
                "今日中に済ませておくべき。",
                "いますぐ済ませるべき。時間がない。",
            ],
        ),
    ),
    # ------------------------------------------------ 住まい(27〜33問目)
    Q(
        key="power_outage",
        label="停電のおそれ",
        aspect=ASPECT_HOME,
        question=Noul(
            instructions=(
                f"{_STATE_NOTE} この人の住まいで停電が起きるおそれがあるか。"
                "暴風や雷の強さ、電文の対象地域を手がかりにすること。"
            ),
        ),
    ),
    Q(
        key="window_protection",
        label="窓の養生",
        aspect=ASPECT_HOME,
        question=Noul(
            instructions=(
                f"{_STATE_NOTE} 窓の飛散防止(雨戸・シャッター・養生テープ)をしておくべき状況か。"
                "暴風のおそれと、`person.profile` の住まいの造りを踏まえること。"
            ),
        ),
    ),
    Q(
        key="home_flood_risk",
        label="住まいの浸水",
        aspect=ASPECT_HOME,
        question=Noul(
            instructions=(
                f"{_STATE_NOTE} この人の住まいが浸水するおそれがあるか。"
                "`person.profile` の立地(川の近さ、ハザードマップの想定)を重く見ること。"
            ),
        ),
    ),
    Q(
        key="landslide_risk",
        label="土砂災害",
        aspect=ASPECT_HOME,
        question=Noul(
            instructions=(
                f"{_STATE_NOTE} この人の住まいの周辺で土砂災害のおそれがあるか。"
                "土砂災害警戒情報や大雨の継続、近くの斜面の有無を手がかりにすること。"
            ),
        ),
    ),
    Q(
        key="stay_upper_floor",
        label="上階への移動",
        aspect=ASPECT_HOME,
        question=Noul(
            instructions=(
                f"{_STATE_NOTE} 屋内で安全を確保するために、2階など上の階や"
                "窓から離れた部屋へ移るべき状況か。"
            ),
        ),
    ),
    Q(
        key="water_leak_check",
        label="雨漏り・排水の確認",
        aspect=ASPECT_HOME,
        question=Noul(
            instructions=(
                f"{_STATE_NOTE} 雨どいや排水溝の詰まり、雨漏りの箇所を"
                "事前に確認しておくべき状況か。"
            ),
        ),
    ),
    Q(
        key="home_safety",
        label="住まいの危険度",
        aspect=ASPECT_HOME,
        question=Score(
            instructions=(
                f"{_STATE_NOTE} この人の住まいにいることの危険度を評価せよ。"
                "`person.profile` の立地と建物の造りを踏まえること。"
            ),
            criteria=[
                "危険はない。ふだんどおり過ごせる。",
                "ほぼ危険はない。念のため戸締まりを確認する程度。",
                "多少の注意が要る。窓から離れる、浸水に気をつけるなど。",
                "危険がある。屋内の安全な場所へ移るべき。",
                "とどまるのは危険。避難を考えるべき。",
            ],
        ),
    ),
    # ------------------------------------------------ 周囲(34〜40問目)
    Q(
        key="child_school",
        label="子どもの学校",
        aspect=ASPECT_FAMILY,
        question=Noul(
            instructions=(
                f"{_STATE_NOTE} 子どもの学校や保育園が休校・休園、または"
                "引き取りになる可能性が高い状況か。`person.profile` に子どもがいる場合に考えること。"
                "子どもがいない場合は偽。"
            ),
        ),
    ),
    Q(
        key="elderly_family",
        label="高齢の家族",
        aspect=ASPECT_FAMILY,
        question=Noul(
            instructions=(
                f"{_STATE_NOTE} 離れて暮らす高齢の家族の安全を、いま確認すべき状況か。"
                "その人たちが住む地域が電文の対象に含まれるかを重く見ること。"
                "`person.profile` にそうした家族がいなければ偽。"
            ),
        ),
    ),
    Q(
        key="pet_care",
        label="ペット",
        aspect=ASPECT_FAMILY,
        question=Noul(
            instructions=(
                f"{_STATE_NOTE} ペットのための対応(散歩を控える、屋内に入れる、"
                "餌や水を確保する)が必要な状況か。`person.profile` にペットがいなければ偽。"
            ),
        ),
    ),
    Q(
        key="neighbor_help",
        label="近所への声かけ",
        aspect=ASPECT_FAMILY,
        question=Noul(
            instructions=(
                f"{_STATE_NOTE} 近所の高齢者や一人暮らしの人に声をかけるべき状況か。"
                "地域全体に危険が及ぶ段階かどうかで判断せよ。"
            ),
        ),
    ),
    Q(
        key="share_info",
        label="情報の共有",
        aspect=ASPECT_FAMILY,
        question=Noul(
            instructions=(
                f"{_STATE_NOTE} この情報を家族や同居人に共有すべき状況か。"
                "共有することで相手の行動が変わるかどうかで判断せよ。"
            ),
        ),
    ),
    Q(
        key="meeting_point",
        label="連絡手段の確認",
        aspect=ASPECT_FAMILY,
        question=Noul(
            instructions=(
                f"{_STATE_NOTE} 家族との連絡手段や合流場所を、いま決めておくべき状況か。"
                "通信が使えなくなる可能性も考えること。"
            ),
        ),
    ),
    Q(
        key="family_concern",
        label="家族への心配度",
        aspect=ASPECT_FAMILY,
        question=Score(
            instructions=(
                f"{_STATE_NOTE} この人の家族(同居・別居を問わない)について、"
                "どれだけ心配すべき状況かを評価せよ。"
            ),
            criteria=[
                "心配は要らない。",
                "ほとんど心配は要らない。",
                "一度連絡して様子を聞くとよい。",
                "連絡を取り、具体的な備えを勧めるべき。",
                "すぐに連絡し、安全の確保を促すべき。",
            ],
        ),
    ),
    # ------------------------------------------------ 仕事(41〜46問目)
    Q(
        key="work_from_home",
        label="在宅勤務",
        aspect=ASPECT_WORK,
        question=Noul(
            instructions=(
                f"{_STATE_NOTE} この日は在宅勤務に切り替えるべき状況か。"
                "`person.profile` に在宅勤務ができると書かれている場合に考えること。"
            ),
        ),
    ),
    Q(
        key="commute_avoid",
        label="出社を避ける",
        aspect=ASPECT_WORK,
        question=Noul(
            instructions=(
                f"{_STATE_NOTE} 出社そのものを避けるべき状況か。"
                "通勤時間帯に交通が乱れる、または危険が及ぶかどうかで判断せよ。"
            ),
        ),
    ),
    Q(
        key="commute_shift",
        label="時差通勤",
        aspect=ASPECT_WORK,
        question=Noul(
            instructions=(
                f"{_STATE_NOTE} 出社するとしても、時間をずらすべき状況か。"
                "荒れる時間帯を避けられるかどうかで判断せよ。"
            ),
        ),
    ),
    Q(
        key="reschedule_meeting",
        label="打ち合わせの調整",
        aspect=ASPECT_WORK,
        question=Noul(
            instructions=(
                f"{_STATE_NOTE} 対面の打ち合わせや予定を、オンラインへ切り替える、"
                "または日程を動かすべき状況か。"
            ),
        ),
    ),
    Q(
        key="notify_workplace",
        label="職場への連絡",
        aspect=ASPECT_WORK,
        question=Noul(
            instructions=(
                f"{_STATE_NOTE} 職場に、遅れる見込みや在宅に切り替える旨を"
                "前もって連絡しておくべき状況か。"
            ),
        ),
    ),
    Q(
        key="work_impact",
        label="仕事への影響度",
        aspect=ASPECT_WORK,
        question=Score(
            instructions=(
                f"{_STATE_NOTE} この人の仕事への影響度を評価せよ。"
                "通勤、対面の予定、在宅勤務のしやすさを踏まえること。"
            ),
            criteria=[
                "影響はない。",
                "わずかな影響。早めに家を出る程度。",
                "ある程度の影響。時間や手段の調整が要る。",
                "大きな影響。在宅への切り替えや予定の変更が要る。",
                "業務の継続が難しい。仕事より安全を優先すべき。",
            ],
        ),
    ),
    # ------------------------------------------------ まとめ(47〜50問目)
    Q(
        key="time_pressure",
        label="残り時間の余裕",
        aspect=ASPECT_ACTION,
        question=Score(
            instructions=(
                f"{_STATE_NOTE} 事象が影響を及ぼすまでに、この人がどれだけ時間の"
                "余裕を持っているかを評価せよ。数字が大きいほど切迫している。"
            ),
            criteria=[
                "十分に時間がある。数日先の話。",
                "まだ余裕がある。明日以降の話。",
                "今日中に動けばよい。",
                "数時間のうちに動くべき。",
                "いますぐ動くべき。時間がない。",
            ],
        ),
    ),
    Q(
        key="need_watch",
        label="続報を追うべき",
        aspect=ASPECT_ACTION,
        question=Noul(
            instructions=(
                f"{_STATE_NOTE} この人は、今後の続報をこまめに確認すべき状況か。"
                "状況が変わる見込みがあるかどうかで判断せよ。"
            ),
        ),
    ),
    Q(
        key="evacuation_consider",
        label="避難の検討",
        aspect=ASPECT_ACTION,
        question=Noul(
            instructions=(
                f"{_STATE_NOTE} この人は避難(自宅を離れること)を具体的に検討すべき状況か。"
                "`person.profile` の立地と、電文が示す危険の大きさを踏まえること。"
            ),
        ),
    ),
    Q(
        key="urgency",
        label="全体の緊急度",
        aspect=ASPECT_ACTION,
        question=Score(
            instructions=(
                f"{_STATE_NOTE} この電文がこの人にとって持つ緊急度を、全体として評価せよ。"
            ),
            criteria=[
                "緊急性はない。知っておく程度でよい。",
                "低い。気に留めておけばよい。",
                "中くらい。今日のうちに何か手を打つとよい。",
                "高い。すぐに具体的な行動を取るべき。",
                "非常に高い。安全の確保を最優先にすべき。",
            ],
        ),
    ),
]


# 画面から切り替えられる質問数。QUESTIONS の先頭から順に使う。
# 増やしたい場合は、QUESTIONS に足したうえでここに数を加える。
QUESTION_SET_SIZES: tuple[int, ...] = (10, 30, 50)

DEFAULT_QUESTION_COUNT = 10


def select_questions(count: int | None = None) -> list[Q]:
    """先頭から count 問を取り出す。None なら全問。

    並び順がそのまま出題順になるので、先頭10問は基本の10問のままにしてある。
    """
    if count is None:
        return list(QUESTIONS)
    return QUESTIONS[: max(1, min(count, len(QUESTIONS)))]


def build_questions(count: int | None = None) -> dict[str, Choice | Score | Noul]:
    """system_one に渡す辞書を組み立てる。全問を1リクエストで送る。"""
    return {q.key: q.question for q in select_questions(count)}


def scale_max(question: Choice | Score | Noul) -> int | None:
    """Score の目盛りの最大値。criteria が5段階なら 0〜4 なので 4 を返す。

    画面で「2.82 / 4」のように範囲を併記するために使う。
    """
    if isinstance(question, Score):
        return max(0, len(question.criteria) - 1)
    return None


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
