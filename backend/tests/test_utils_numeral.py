"""Unit tests for the Chinese numeral converter utility."""

from app.utils.numeral import cn_to_int


class TestCnToIntDigits:
    def test_zero(self):
        assert cn_to_int("零") == 0

    def test_one(self):
        assert cn_to_int("一") == 1

    def test_nine(self):
        assert cn_to_int("九") == 9


class TestCnToIntTens:
    def test_ten(self):
        assert cn_to_int("十") == 10

    def test_twelve(self):
        assert cn_to_int("十二") == 12

    def test_twenty(self):
        assert cn_to_int("二十") == 20

    def test_twenty_one(self):
        assert cn_to_int("二十一") == 21

    def test_ninety_nine(self):
        assert cn_to_int("九十九") == 99


class TestCnToIntHundreds:
    def test_one_hundred(self):
        assert cn_to_int("一百") == 100

    def test_one_hundred_zero_one(self):
        assert cn_to_int("一百零一") == 101

    def test_one_hundred_twenty_three(self):
        assert cn_to_int("一百二十三") == 123

    def test_nine_hundred_ninety_nine(self):
        assert cn_to_int("九百九十九") == 999


class TestCnToIntThousands:
    def test_one_thousand(self):
        assert cn_to_int("一千") == 1000

    def test_one_thousand_zero_one(self):
        assert cn_to_int("一千零一") == 1001

    def test_one_thousand_one_hundred(self):
        assert cn_to_int("一千一百") == 1100

    def test_one_thousand_two_hundred_thirty_four(self):
        assert cn_to_int("一千二百三十四") == 1234

    def test_nine_thousand_nine_hundred_ninety_nine(self):
        assert cn_to_int("九千九百九十九") == 9999


class TestCnToIntEdgeCases:
    def test_empty_string(self):
        assert cn_to_int("") is None

    def test_invalid_character(self):
        assert cn_to_int("abc") is None

    def test_invalid_mixed_arabic(self):
        assert cn_to_int("一2三") is None

    def test_unsupported_unit(self):
        assert cn_to_int("一万一千") is None

    def test_only_unit(self):
        assert cn_to_int("十") == 10
        assert cn_to_int("百") is None
        assert cn_to_int("千") is None
