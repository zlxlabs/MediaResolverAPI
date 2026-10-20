"""中文判定按汉字字符数量计算。"""

from app.utils.validators import is_chinese_text


def test_chinese_sentences_are_counted_by_character():
    assert is_chinese_text("这是一个完整的中文句子没有标点") is True
    assert is_chinese_text("今天天气很好，我们去公园散步吧。") is True


def test_english_and_empty_text_are_not_chinese():
    assert is_chinese_text("Hello world, this is English") is False
    assert is_chinese_text("") is False
