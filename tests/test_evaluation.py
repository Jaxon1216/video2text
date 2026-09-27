from v2t.evaluation import character_error_rate, edit_distance, normalize_for_cer, term_recall


def test_normalize_drops_punctuation_whitespace_and_width() -> None:
    assert normalize_for_cer("线程池，是什么？ ＡＰＩ Hooks!") == "线程池是什么apihooks"


def test_edit_distance_basic_cases() -> None:
    assert edit_distance("kitten", "sitting") == 3
    assert edit_distance("", "abc") == 3
    assert edit_distance("线程池", "现成时") == 3


def test_character_error_rate_ignores_punctuation_differences() -> None:
    assert character_error_rate("线程池，核心线程。", "线程池核心线程") == 0.0
    assert character_error_rate("线程池", "现成池") == 2 / 3
    assert character_error_rate("", "") == 0.0
    assert character_error_rate("", "多余") == 1.0


def test_term_recall_is_case_and_width_insensitive() -> None:
    hits, total, missed = term_recall(["线程池", "ThreadPoolExecutor", "阻塞队列", ""], "用threadpoolexecutor创建线程池")
    assert (hits, total) == (2, 3)
    assert missed == ["阻塞队列"]
