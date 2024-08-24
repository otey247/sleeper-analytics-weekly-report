__author__ = "Wren J. R. (otey247)"
__email__ = "otey247@otey247.dev"

import json
import logging
import os
import sys
import urllib.request
from copy import deepcopy
from pathlib import Path
from random import choice
from typing import List, Dict, Tuple, Callable, Any, Union
from urllib.error import URLError

from PIL import Image
from PIL import ImageFile
from reportlab.graphics.shapes import Line, Drawing
from reportlab.lib import colors
from reportlab.lib import styles
from reportlab.lib.enums import TA_CENTER, TA_RIGHT, TA_LEFT
from reportlab.lib.pagesizes import LETTER, portrait
from reportlab.lib.pagesizes import inch
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, PageBreak, Spacer, Flowable
from reportlab.platypus.flowables import Image as ReportLabImage, KeepTogether

from dao.base import BaseLeague, BaseTeam, BasePlayer
from report.data import ReportData
from report.pdf.charts.bar import HorizontalBarChart3DGenerator
from report.pdf.charts.line import LineChartGenerator
from report.pdf.charts.pie import BreakdownPieDrawing
from resources.documentation import descriptions
from utilities.logger import get_logger
from utilities.settings import settings
from utilities.utils import truncate_cell_for_display

logger = get_logger(__name__, propagate=False)

# suppress verbose PIL debug logging
logging.getLogger("PIL.PngImagePlugin").setLevel(level=logging.INFO)


def get_player_image(url: str, data_dir: Path, week: int, image_quality: int, width: float = 1.0 * inch,
                     player_name: str = None, offline: bool = False):
    headshots_dir = Path(data_dir) / f"week_{week}" / "player_headshots"

    if not Path(headshots_dir).exists():
        os.makedirs(headshots_dir)

    if url:
        img_name = url.split("/")[-1]
        local_img_path = Path(headshots_dir) / img_name
        local_img_jpg_path = Path(headshots_dir) / f"{img_name.split('.')[0]}.jpg"

        if not Path(local_img_jpg_path).exists():
            if not Path(local_img_path).exists():
                if not offline:
                    logger.debug(f"Retrieving player headshot for \"{player_name}\"")
                    try:
                        urllib.request.urlretrieve(url, local_img_path)
                    except URLError:
                        logger.error(
                            f"Unable to retrieve player "
                            f"headshot{f' for player {player_name}' if player_name else ''} at url {url}"
                        )
                        local_img_path = Path("resources") / "images" / "photo-not-available.png"
                else:
                    logger.error(
                        f"FILE {local_img_jpg_path} DOES NOT EXIST. CANNOT LOAD DATA LOCALLY WITHOUT HAVING PREVIOUSLY "
                        f"SAVED DATA!")
                    sys.exit(1)

            img = Image.open(local_img_path)
            if img.mode != "RGBA":
                img = img.convert("RGBA")

            # Create a white rgba background
            background = Image.new("RGB", img.size, "WHITE")
            background.paste(img, (0, 0), img)
            img = background
            img = img.convert("RGB")
            local_img_path = local_img_jpg_path
            img.save(local_img_path, quality=image_quality, optimize=True)
        else:
            local_img_path = local_img_jpg_path

    else:
        logger.error(f"No available URL for player{f' {player_name}' if player_name else ''}.")
        img_name = "photo-not-available.png"
        local_img_path = Path("resources") / "images" / img_name

    img_reader = ImageReader(local_img_path)
    iw, ih = img_reader.getSize()
    aspect = ih / float(iw)

    ImageFile.LOAD_TRUNCATED_IMAGES = True

    scaled_img = ReportLabImage(local_img_path, width=width, height=(width * aspect))

    return scaled_img


# noinspection PyPep8Naming
class HyperlinkedImage(ReportLabImage, object):
    """Class written by Dennis Golomazov on stackoverflow here: https://stackoverflow.com/a/39134216
    Adopted from https://stackoverflow.com/a/26294527/304209.
    """

    def __init__(self, file_path: Path, hyperlink: str = None, width: int = None, height: int = None,
                 kind: str = 'direct', mask: str = 'auto', lazy: int = 1, hAlign: str = 'CENTER'):
        """The only variable added to __init__() is hyperlink.

        It defaults to None for the if statement used later.
        """
        super(HyperlinkedImage, self).__init__(file_path, width, height, kind, mask, lazy,
                                               hAlign=hAlign)
        self.hyperlink = hyperlink

    def drawOn(self, canvas: Canvas, x: int, y: int, _sW: int = 0):
        if self.hyperlink:  # If a hyperlink is given, create a canvas.linkURL()
            # This is basically adjusting the x coordinate according to the alignment
            # given to the flowable (RIGHT, LEFT, CENTER)
            x1 = self._hAlignAdjust(x, _sW)
            y1 = y
            x2 = x1 + self._width
            y2 = y1 + self._height
            canvas.linkURL(url=self.hyperlink, rect=(x1, y1, x2, y2), thickness=0, relative=1)
        super(HyperlinkedImage, self).drawOn(canvas, x, y, _sW)


class PdfGenerator(object):

    # noinspection SpellCheckingInspection
    def __init__(self, season: int, league: BaseLeague, playoff_prob_sims: int,
                 report_title_text: str, report_footer_text: str, report_data: ReportData):
        logger.debug("Instantiating PDF generator.")

        # report settings
        self.season = season
        self.league_id = league.league_id
        self.playoff_slots = int(league.num_playoff_slots)
        self.num_regular_season_weeks = int(league.num_regular_season_weeks)
        self.week_for_report = league.week_for_report
        self.data_dir = Path(league.data_dir) / str(league.season) / league.league_id
        self.break_ties = report_data.break_ties
        self.playoff_prob_sims = playoff_prob_sims
        self.num_coaching_efficiency_dqs = report_data.num_coaching_efficiency_dqs

        # table column widths
        # ..........................Place/Rank...Team.........Manager......Col 4.....
        self.widths_04_cols_no_1 = [1.00 * inch, 2.50 * inch, 2.50 * inch, 1.75 * inch]  # 7.75

        # ..........................Place........Team.........Manager......Col 4.....
        self.widths_04_cols_no_2 = [0.45 * inch, 2.30 * inch, 1.75 * inch, 3.25 * inch]  # 7.75

        # ..........................Place........Team.........Manager......Metric.......Avg.......
        self.widths_05_cols_no_1 = [0.45 * inch, 1.95 * inch, 1.85 * inch, 1.75 * inch, 1.75 * inch]  # 7.75

        # ..........................Place........Team.........Manager......Col 4........Col 5........Col 6.....
        self.widths_06_cols_no_1 = [0.45 * inch, 1.95 * inch, 1.85 * inch, 1.00 * inch, 1.50 * inch,
                                    1.00 * inch]  # 7.75

        # ..........................Place........Team.........Manager......Col 4........Col 5........Col 6.....
        self.widths_06_cols_no_2 = [0.45 * inch, 1.95 * inch, 1.35 * inch, 1.00 * inch, 2.00 * inch,
                                    1.00 * inch]  # 7.75

        # ..........................Place........Team.........Manager......Col 4........Col 5........Col 6.....
        self.widths_06_cols_no_3 = [0.45 * inch, 1.95 * inch, 1.85 * inch, 0.60 * inch, 1.45 * inch,
                                    1.45 * inch]  # 7.75

        # ..........................Place........Team.........Manager......Col 4........Col 5........Col 6........Col 7.....
        self.widths_07_cols_no_1 = [0.45 * inch, 1.80 * inch, 1.50 * inch, 0.75 * inch, 1.50 * inch, 0.75 * inch,
                                    1.00 * inch]  # 7.75

        # ..........................Place........Team.........Manager......Col 4........Col 5........Col 6........Col 7.....
        self.widths_07_cols_no_2 = [0.45 * inch, 1.80 * inch, 1.50 * inch, 1.10 * inch, 1.00 * inch, 1.05 * inch,
                                    0.85 * inch]  # 7.75

        # ..........................Place........Team.........Manager......Record.......Pts For......Pts Against..Streak.......Waiver.......Moves........Trades....
        self.widths_10_cols_no_1 = [0.45 * inch, 1.80 * inch, 1.10 * inch, 1.00 * inch, 0.80 * inch, 1.05 * inch,
                                    0.50 * inch, 0.50 * inch, 0.50 * inch, 0.50 * inch]  # 8.20

        # ..........................Place........Team.........Manager......Record.......Div Rec......Pts For......Pts Against..Streak.......Waiver.......Moves........Trades....
        self.widths_11_cols_no_1 = [0.40 * inch, 1.65 * inch, 0.80 * inch, 0.85 * inch, 0.85 * inch, 0.80 * inch,
                                    0.90 * inch, 0.45 * inch, 0.50 * inch, 0.50 * inch, 0.50 * inch]  # 8.20

        # ..........................Place........Team.........Manager......Record.......Pts For......Pts Against..Streak.......Waiver.......FAAB.........Moves........Trades....
        self.widths_11_cols_no_2 = [0.40 * inch, 1.65 * inch, 0.90 * inch, 1.00 * inch, 0.80 * inch, 1.05 * inch,
                                    0.45 * inch, 0.50 * inch, 0.45 * inch, 0.50 * inch, 0.50 * inch]  # 8.20

        # ..........................Place........Team.........Manager......Record.......Div Rec......Pts For......Pts Against..Streak.......Waiver.......FAAB.........Moves........Trades...
        self.widths_12_cols_no_1 = [0.40 * inch, 1.55 * inch, 0.85 * inch, 0.85 * inch, 0.85 * inch, 0.65 * inch,
                                    0.65 * inch, 0.45 * inch, 0.50 * inch, 0.45 * inch, 0.50 * inch,
                                    0.50 * inch]  # 8.20

        if self.playoff_slots > 0:
            # .........................Team.........Manager......Record.......Pts For......Pts Against....Finish Positions..............................................
            self.widths_n_cols_no_1 = [1.55 * inch, 1.00 * inch, 0.95 * inch, 0.65 * inch, 0.65 * inch] + [
                round(3.4 / self.playoff_slots, 2) * inch] * self.playoff_slots  # 8.20

        self.line_separator = Drawing(100, 1)
        self.line_separator.add(Line(0, -65, 550, -65, strokeColor=colors.black, strokeWidth=1))
        self.spacer_twentieth_inch = Spacer(1, 0.05 * inch)
        self.spacer_tenth_inch = Spacer(1, 0.10 * inch)
        self.spacer_quarter_inch = Spacer(1, 0.25 * inch)
        self.spacer_half_inch = Spacer(1, 0.50 * inch)
        self.spacer_three_inch = Spacer(1, 3.00 * inch)
        self.spacer_five_inch = Spacer(1, 5.00 * inch)

        # set text styles
        self.font_size = settings.report_settings.font_size
        font_key = settings.report_settings.font
        supported_fonts_list = settings.report_settings.supported_fonts_list
        if font_key not in supported_fonts_list:
            logger.warning(
                f"The {font_key} font is not supported at this time. Report formatting has defaulted to Helvetica. "
                f"Please try again with one of the following supported font keys: {supported_fonts_list}"
            )
        font_dict = {
            "helvetica": 1,
            "times": 2,
            "symbola": 3,
            "opensansemoji": 4,
            "sketchcollege": 5,
            "leaguegothic": 6
        }
        if font_key in font_dict.keys():
            which_font = font_dict[font_key]
        else:
            which_font = 0
        use_custom_font = False if which_font < 3 else True

        if which_font == 1:
            self.font = "Helvetica"
            self.font_bold = "Helvetica-Bold"
            self.font_italic = "Helvetica-Oblique"
            self.font_bold_italic = "Helvetica-BoldOblique"
        elif which_font == 2:
            self.font = "Times-Roman"
            self.font_bold = "Times-Bold"
            self.font_italic = "Times-Italic"
            self.font_bold_italic = "Times-BoldItalic"
        # set custom font(s)
        elif use_custom_font and which_font == 3:
            self.font = "Symbola"
            self.font_bold = "Symbola"
            self.font_italic = "Symbola"
            self.font_bold_italic = "Symbola"
        elif use_custom_font and which_font == 4:
            self.font = "OpenSansEmoji"
            self.font_bold = "OpenSansEmoji"
            self.font_italic = "OpenSansEmoji"
            self.font_bold_italic = "OpenSansEmoji"
        elif use_custom_font and which_font == 5:
            self.font = "SketchCollege"
            self.font_bold = "SketchCollege"
            self.font_italic = "SketchCollege"
            self.font_bold_italic = "SketchCollege"
        elif use_custom_font and which_font == 6:
            self.font = "LeagueGothicRegular"
            self.font_bold = "LeagueGothicRegular"
            self.font_italic = "LeagueGothicItalic"
            self.font_bold_italic = "LeagueGothicItalic"
        else:
            # default to Helvetica
            self.font = "Helvetica"
            self.font_bold = "Helvetica-Bold"
            self.font_italic = "Helvetica-Oblique"
            self.font_bold_italic = "Helvetica-BoldOblique"

        if use_custom_font:
            pdfmetrics.registerFont(TTFont(self.font, "resources/fonts/" + self.font + ".ttf"))
            pdfmetrics.registerFont(TTFont(self.font_bold, "resources/fonts/" + self.font + ".ttf"))
            pdfmetrics.registerFont(TTFont(self.font_italic, "resources/fonts/" + self.font + ".ttf"))
            pdfmetrics.registerFont(TTFont(self.font_bold_italic, "resources/fonts/" + self.font + ".ttf"))

        styles._baseFontName = self.font
        self.stylesheet = styles.getSampleStyleSheet()
        self.stylesheet.add(
            ParagraphStyle(
                name="HC",
                parent=self.stylesheet["Normal"],
                fontSize=self.font_size + 2,
                font=self.font,
                alignment=TA_CENTER,
                spaceAfter=6
            ),
            alias="header-centered"
        )
        self.text_style_title = self.stylesheet["HC"]
        self.text_style = self.stylesheet["BodyText"]
        self.text_style_normal = self.stylesheet["Normal"]
        self.text_style_h1 = self.stylesheet["Heading1"]
        self.text_style_h1.fontName = self.font
        self.text_style_h1.fontSize = self.font_size + 2
        self.text_style_h2 = self.stylesheet["Heading2"]
        self.text_style_h2.fontName = self.font
        self.text_style_h3 = self.stylesheet["Heading3"]
        self.text_style_h3.fontName = self.font
        self.text_style_subtitles = ParagraphStyle(
            name="subtitles",
            parent=self.text_style_normal,
            # fontName=tt2ps(bfn, 1, 1),
            fontName=self.font_bold_italic,
            fontSize=self.font_size - 4,
            leading=10,
            spaceBefore=0,
            spaceAfter=0
        )
        self.text_style_subsubtitles = ParagraphStyle(
            name="subsubtitles",
            parent=self.text_style_normal,
            # fontName=tt2ps(bfn, 1, 1),
            fontName=self.font_bold,
            fontSize=self.font_size - 2,
            textColor=colors.orangered,
            leading=10,
            spaceBefore=0,
            spaceAfter=0
        )
        self.text_style_italics = ParagraphStyle(
            name="italics",
            fontSize=10,
            alignment=TA_CENTER,
            fontName=self.font_italic
        )
        self.text_style_small = ParagraphStyle(name="small", fontSize=5, alignment=TA_CENTER)
        self.text_style_invisible = ParagraphStyle(name="invisible", fontSize=0, textColor=colors.white)

        # set word wrap
        self.text_style.wordWrap = "CJK"

        title_table_style_list = [
            ("TEXTCOLOR", (0, 0), (-1, -1), colors.black),
            ("FONT", (0, 0), (-1, -1), self.font),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("VALIGN", (0, 0), (-1, 0), "MIDDLE"),
        ]

        self.title_style = TableStyle(title_table_style_list)

        header_table_style_list = [
            ("TEXTCOLOR", (0, 0), (-1, -1), colors.black),
            ("FONT", (0, 0), (-1, -1), self.font),
            ("ALIGN", (1, 0), (1, -1), "RIGHT"),
            ("VALIGN", (0, 0), (-1, 0), "MIDDLE"),
        ]

        self.header_style = TableStyle(header_table_style_list)

        # Reportlab fonts: https://github.com/mattjmorrison/ReportLab/blob/master/src/reportlab/lib/fonts.py
        table_style_list = [
            ("TEXTCOLOR", (0, 1), (-1, 1), colors.green),
            ("FONT", (0, 0), (-1, -1), self.font),
            ("FONT", (0, 1), (-1, 1), self.font_italic),
            ("FONT", (0, 0), (-1, 0), self.font_bold),
            ("FONTSIZE", (0, 0), (-1, -1), self.font_size - 2),
            ("TOPPADDING", (0, 0), (-1, -1), 1),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.gray),
            ("GRID", (0, 0), (-1, 0), 1.5, colors.black),
            ("BOX", (0, 0), (-1, -1), 0.5, colors.black),
            ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.black),
            ("VALIGN", (0, 0), (-1, 0), "MIDDLE"),
            ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey)
        ]

        # general table style
        self.style = TableStyle(table_style_list)

        # table style for left aligned final column
        style_left_alight_right_col_list = deepcopy(table_style_list)
        style_left_alight_right_col_list.append(("ALIGN", (-1, 1), (-1, -1), "LEFT"))
        self.style_left_align_right_col = TableStyle(style_left_alight_right_col_list)

        # table style without any color highlighting on the first line
        no_highlight = deepcopy(table_style_list[4:])
        no_highlight.append(("FONT", (0, 0), (-1, -1), self.font))
        self.style_no_highlight = TableStyle(no_highlight)

        # table style with red highlighting on the first line
        red_highlight = deepcopy(table_style_list)
        red_highlight[0] = ("TEXTCOLOR", (0, 1), (-1, 1), colors.darkred)
        self.style_red_highlight = TableStyle(red_highlight)

        boom_bust_table_style_list = [
            ("TEXTCOLOR", (0, 0), (0, -1), colors.green),
            ("TEXTCOLOR", (1, 0), (1, -1), colors.darkred),
            ("FONT", (0, 0), (-1, -1), self.font),
            ("FONT", (0, 0), (-1, -1), self.font_bold),
            ("FONT", (0, 1), (-1, 1), self.font_italic),
            ("FONTSIZE", (0, 0), (-1, 0), self.font_size + 4),
            ("FONTSIZE", (0, 1), (-1, -2), self.font_size + 2),
            ("FONTSIZE", (0, -1), (-1, -1), self.font_size + 6),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("VALIGN", (0, 0), (-1, 0), "MIDDLE"),
        ]
        self.boom_bust_table_style = TableStyle(boom_bust_table_style_list)

        # report specific document elements
        self.standings_headers = [
            ["Place", "Team", "Manager", "Record", "Points For", "Points Against", "Streak", "Waiver", "Moves",
             "Trades"]
        ]
        self.median_standings_headers = [
            ["Place", "Team", "Manager", "Combined Record", "Median Record", "Season +/- Median", "Median Streak"]
        ]

        ordinal_dict = {
            1: "1st", 2: "2nd", 3: "3rd", 4: "4th",
            5: "5th", 6: "6th", 7: "7th", 8: "8th",
            9: "9th", 10: "10th", 11: "11th", 12: "12th",
            13: "13th", 14: "14th", 15: "15th", 16: "16th",
            17: "17th", 18: "18th", 19: "19th", 20: "20th"
        }
        ordinal_list = []
        playoff_places = 1
        while playoff_places <= self.playoff_slots:
            ordinal_list.append(ordinal_dict[playoff_places])
            playoff_places += 1
        self.playoff_probs_headers = [
            ["Team", "Manager", "Record", "Playoffs", "Needed"] + ordinal_list
        ]
        self.power_ranking_headers = [["Power Rank", "Team", "Manager", "Season Avg. (Place)"]]
        self.zscores_headers = [["Place", "Team", "Manager", "Z-Score"]]
        self.scores_headers = [["Place", "Team", "Manager", "Points", "Season Avg. (Place)"]]
        self.efficiency_headers = [["Place", "Team", "Manager", "Coaching Efficiency (%)", "Season Avg. (Place)"]]
        self.luck_headers = [["Place", "Team", "Manager", "Luck", "Season Avg. (Place)", "Weekly Record (W-L)"]]
        self.optimal_scores_headers = [["Place", "Team", "Manager", "Optimal Points", "Season Total"]]
        self.bad_boy_headers = [["Place", "Team", "Manager", "Bad Boy Pts", "Worst Offense", "# Offenders"]]
        self.beef_headers = [["Place", "Team", "Manager", "TABBU(s)"]]
        self.weekly_top_scorer_headers = [["Week", "Team", "Manager", "Score"]]
        self.weekly_highest_ce_headers = [["Week", "Team", "Manager", "Coaching Efficiency (%)"]]
        self.tie_for_first_footer = "<i>&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;*Tie(s).</i>"

        # options: "document", "section", or None
        self.report_title = self.create_title(report_title_text, element_type="document")

        footer_title = [
            [self.spacer_three_inch],
            [Paragraph(report_footer_text, self.text_style_normal)],
        ]
        footer_data = [
            [
                [
                    self.get_img(
                        "resources/images/donate-paypal.png",
                        hyperlink="https://www.paypal.com/cgi-bin/webscr?cmd=_donations&business=VZZCNLRHH9BQS"
                    )
                ],
                [
                    self.get_img(
                        "resources/images/donate-bitcoin.png",
                        hyperlink="https://blockstream.info/address/bc1qataspvklhewtswm357m0677q4raag5new2xt3e"
                    )
                ],
                [
                    self.get_img(
                        "resources/images/donate-ethereum.png",
                        hyperlink="https://etherscan.io/address/0x5eAa522e66a90577D49e9E72f253EC952CDB4059"
                    )
                ]
            ],
            [
                [
                    self.get_img(
                        "resources/images/donate-paypal-qr.png",
                        hyperlink="https://www.paypal.com/cgi-bin/webscr?cmd=_donations&business=VZZCNLRHH9BQS"
                    )
                ],
                [
                    self.get_img(
                        "resources/images/donate-bitcoin-qr.png",
                        hyperlink="https://blockstream.info/address/bc1qataspvklhewtswm357m0677q4raag5new2xt3e"
                    )
                ],
                [
                    self.get_img(
                        "resources/images/donate-ethereum-qr.png",
                        hyperlink="https://etherscan.io/address/0x5eAa522e66a90577D49e9E72f253EC952CDB4059"
                    )
                ]
            ],
            [
                Paragraph("PayPal", self.text_style_small),
                Paragraph("bc1qataspvklhewtswm357m0677q4raag5new2xt3e", self.text_style_small),
                Paragraph("0x5eAa522e66a90577D49e9E72f253EC952CDB4059", self.text_style_small)
            ]
        ]
        self.report_footer_title = Table(footer_title, colWidths=7.75 * inch, style=self.title_style)
        self.report_footer = Table(footer_data, colWidths=2.50 * inch, style=self.title_style)

        # data for report
        self.report_data = report_data
        self.data_for_median_standings = report_data.data_for_current_median_standings
        self.data_for_scores = report_data.data_for_scores
        self.data_for_coaching_efficiency = report_data.data_for_coaching_efficiency
        self.data_for_luck = report_data.data_for_luck
        self.data_for_optimal_scores = report_data.data_for_optimal_scores
        self.data_for_power_rankings = report_data.data_for_power_rankings
        self.data_for_z_scores = report_data.data_for_z_scores
        self.data_for_bad_boy_rankings = report_data.data_for_bad_boy_rankings
        self.data_for_beef_rankings = report_data.data_for_beef_rankings
        self.data_for_weekly_points_by_position = report_data.data_for_weekly_points_by_position
        self.data_for_season_average_team_points_by_position = report_data.data_for_season_avg_points_by_position
        self.data_for_season_weekly_top_scorers = report_data.data_for_season_weekly_top_scorers
        self.data_for_season_weekly_highest_ce = report_data.data_for_season_weekly_highest_ce

        # dynamically create table styles based on number of ties in metrics
        self.style_efficiency_dqs = None
        self.style_tied_scores = self.set_tied_values_style(
            self.report_data.ties_for_scores, table_style_list, "scores"
        )
        self.style_tied_efficiencies = self.set_tied_values_style(
            self.report_data.ties_for_coaching_efficiency, table_style_list, "coaching_efficiency"
        )
        self.style_tied_luck = self.set_tied_values_style(self.report_data.ties_for_luck, table_style_list, "luck")
        self.style_tied_power_rankings = self.set_tied_values_style(
            self.report_data.ties_for_power_rankings, table_style_list, "power_ranking"
        )
        self.style_tied_bad_boy = self.set_tied_values_style(
            self.report_data.ties_for_power_rankings, table_style_list, "bad_boy"
        )
        self.style_tied_beef = self.set_tied_values_style(
            self.report_data.ties_for_beef_rankings, style_left_alight_right_col_list, "beef"
        )

        # table of contents
        self.toc = TableOfContents(self.font, self.font_size, self.break_ties)

        # appendix
        self.appendix = Appendix(
            "Appendix I: Rankings & Metrics", self.create_title, self.toc.get_current_anchor, self.font_size,
            self.text_style
        )

        # team data for use on team specific stats pages
        self.teams_results = report_data.teams_results

    # noinspection PyUnusedLocal
    def add_page_number(self, canvas: Canvas, doc: SimpleDocTemplate):
        """
        Add the page number
        """
        page_num = canvas.getPageNumber()
        text = f"Page {page_num}"
        canvas.setFont(self.font, self.font_size - 4)
        canvas.drawRightString(4.45 * inch, 0.25 * inch, text)

    def add_page_break(self):
        self.toc.add_toc_page()
        return PageBreak()

    def set_tied_values_style(self, num_ties: int, table_style_list: List[Tuple[Any]], metric_type: str):

        num_first_places = num_ties
        if metric_type == "scores":
            if not self.report_data.num_first_place_for_score > 0:
                num_first_places = 0
            else:
                num_first_places = self.report_data.num_first_place_for_score
        elif metric_type == "coaching_efficiency":
            if not self.report_data.num_first_place_for_coaching_efficiency > 0:
                num_first_places = 0
            else:
                num_first_places = self.report_data.num_first_place_for_coaching_efficiency
        elif metric_type == "luck":
            if not self.report_data.num_first_place_for_luck > 0:
                num_first_places = 0
            else:
                num_first_places = self.report_data.num_first_place_for_luck
        elif metric_type == "power_ranking":
            if not self.report_data.ties_for_first_for_power_rankings > 0:
                num_first_places = 0
            else:
                num_first_places = self.report_data.ties_for_first_for_power_rankings
        elif metric_type == "bad_boy":
            if not self.report_data.num_first_place_for_bad_boy_rankings > 0:
                num_first_places = 0
            else:
                num_first_places = self.report_data.num_first_place_for_bad_boy_rankings
        elif metric_type == "beef":
            if not self.report_data.num_first_place_for_beef_rankings > 0:
                num_first_places = 0
            else:
                num_first_places = self.report_data.num_first_place_for_beef_rankings

        tied_values_table_style_list = list(table_style_list)
        if metric_type == "scores" and self.break_ties:
            tied_values_table_style_list.append(("TEXTCOLOR", (0, 1), (-1, 1), colors.green))
            tied_values_table_style_list.append(("FONT", (0, 1), (-1, 1), self.font_italic))
        else:
            iterator = num_first_places
            index = 1
            if metric_type == "bad_boy":
                color = colors.darkred
            else:
                color = colors.green
            while iterator > 0:
                tied_values_table_style_list.append(("TEXTCOLOR", (0, index), (-1, index), color))
                tied_values_table_style_list.append(("FONT", (0, index), (-1, index), self.font_italic))
                iterator -= 1
                index += 1

        if metric_type == "coaching_efficiency":
            if self.num_coaching_efficiency_dqs > 0:
                dq_index = len(self.data_for_scores) - self.num_coaching_efficiency_dqs + 1

                if self.report_data.ties_for_coaching_efficiency > 0:
                    efficiencies_dq_table_style_list = list(tied_values_table_style_list)
                else:
                    efficiencies_dq_table_style_list = list(table_style_list)

                eff_dq_count = self.num_coaching_efficiency_dqs
                while eff_dq_count > 0:
                    efficiencies_dq_table_style_list.append(("TEXTCOLOR", (0, dq_index), (-1, -1), colors.red))
                    eff_dq_count -= 1
                    dq_index += 1
                self.style_efficiency_dqs = TableStyle(efficiencies_dq_table_style_list)

        return TableStyle(tied_values_table_style_list)

    def create_section(self, title_text: str, headers: List[List[str]], data: Any,
                       table_style: TableStyle, table_style_ties: Union[TableStyle, None], col_widths: List[float],
                       subtitle_text: Union[str, List[str]] = None, subsubtitle_text: Union[str, List[str]] = None,
                       header_text: str = None, footer_text: str = None, row_heights: List[List[float]] = None,
                       tied_metric: bool = False, metric_type: str = None,
                       section_title_function: Callable = None) -> KeepTogether:
        logger.debug(
            f"Creating report section: \"{title_text if title_text else metric_type}\" with "
            f"data:\n{json.dumps(data, indent=2)}\n"
        )

        title = None
        if title_text:
            section_anchor = str(self.toc.get_current_anchor())
            self.appendix.add_entry(
                title_text,
                section_anchor,
                getattr(descriptions, title_text.replace(" ", "_").replace("-", "_").lower())
            )
            appendix_anchor = self.appendix.get_last_entry_anchor()
            title = self.create_title(
                f"<a href = #page.html#{appendix_anchor} color=blue><u><b>{title_text}</b></u></a>",
                element_type="section",
                anchor=f"<a name = page.html#{section_anchor}></a>",
                subtitle_text=subtitle_text,
                subsubtitle_text=subsubtitle_text
            )
            if section_title_function:
                section_title_function(title_text)
            else:
                self.toc.add_metric_section(title_text)

        if metric_type == "standings":
            font_reduction = 0
            for x in range(1, (len(data) % 12) + 1, 4):
                font_reduction += 1
            table_style.add("FONTSIZE", (0, 0), (-1, -1), (self.font_size - 2) - font_reduction)
            if self.report_data.is_faab:
                if self.report_data.has_waiver_priorities:
                    if self.report_data.has_divisions:
                        col_widths = self.widths_12_cols_no_1
                    else:
                        col_widths = self.widths_11_cols_no_2

                if "FAAB" not in headers[0]:
                    if self.report_data.has_waiver_priorities:
                        if self.report_data.has_divisions:
                            headers[0].insert(9, "FAAB")
                        else:
                            headers[0].insert(8, "FAAB")
                    else:
                        if self.report_data.has_divisions:
                            headers[0][8] = "FAAB"
                        else:
                            headers[0][7] = "FAAB"

        if metric_type == "playoffs":
            font_reduction = 0
            for x in range(1, (len(data[0][5:]) % 6) + 2):
                font_reduction += 1
            table_style.add("FONTSIZE", (0, 0), (-1, -1), (self.font_size - 2) - font_reduction)

        if metric_type == "scores":
            if self.break_ties and self.report_data.ties_for_scores > 0:
                self.scores_headers[0].append("Bench Points")
            else:
                for index, team in enumerate(self.data_for_scores):
                    self.data_for_scores[index] = team[:-1]

        if metric_type == "coaching_efficiency":
            if self.break_ties and tied_metric:
                self.efficiency_headers[0][3] = "CE (%)"
                self.efficiency_headers[0].extend(["# > Avg.", "Sum % > Avg."])
            else:
                for index, team in enumerate(self.data_for_coaching_efficiency):
                    self.data_for_coaching_efficiency[index] = team

        if metric_type == "top_scorers":
            temp_data = []
            for wk in data:
                entry = [
                    wk["week"],
                    wk["team"],
                    wk["manager"],
                    wk["score"]
                ]
                temp_data.append(entry)
                data = temp_data

        if metric_type == "highest_ce":
            temp_data = []
            for wk in data:
                # noinspection PyTypeChecker
                entry = [
                    wk["week"],
                    wk["team"],
                    wk["manager"],
                    wk["ce"]
                ]
                temp_data.append(entry)
                data = temp_data

        if metric_type == "beef":
            cow_icon = self.get_img(Path("resources") / "images" / "cow.png", width=0.20 * inch)
            beef_icon = self.get_img(Path("resources") / "images" / "beef.png", width=0.20 * inch)
            half_beef_icon = self.get_img(Path("resources") / "images" / "beef-half.png", width=0.10 * inch)

            for team in data:
                num_cows = int(float(team[3]) // 5)
                num_beefs = int(float(team[3]) / 0.5) - (num_cows * 10)

                if num_cows > 0:
                    num_beefs += 10
                    beefs = [cow_icon] * (num_cows - 1)  # subtract one to make sure there are always beef icons
                else:
                    beefs = []

                if num_beefs % 2 == 0:
                    beefs += [beef_icon] * int((num_beefs / 2))
                else:
                    beefs += [beef_icon] * int((num_beefs / 2))
                    beefs.append(half_beef_icon)

                # noinspection PyTypeChecker
                beefs.insert(0, f"{team[-1]} ")
                beefs = [beefs]
                beefs_col_widths = [0.20 * inch] * (num_beefs if num_beefs > 0 else num_cows)
                beefs_col_widths.insert(0, 0.50 * inch)
                tabbu_column_table = Table(beefs, colWidths=beefs_col_widths, rowHeights=0.25 * inch)

                tabbu_column_table_style_list = [
                    ("FONT", (0, 0), (-1, -1), self.font),
                    ("FONTSIZE", (0, 0), (-1, -1), self.font_size - 2)
                ]
                if data.index(team) == 0:
                    tabbu_column_table_style_list.extend([
                        ("TEXTCOLOR", (0, 0), (-1, -1), colors.green),
                        ("FONT", (0, 0), (-1, -1), self.font_italic)
                    ])
                tabbu_column_table.setStyle(TableStyle(tabbu_column_table_style_list))
                team[-1] = tabbu_column_table

        data_table = self.create_data_table(
            metric_type, headers, data, table_style, table_style_ties, col_widths, row_heights, tied_metric
        )

        if metric_type == "coaching_efficiency":
            if self.num_coaching_efficiency_dqs > 0:
                data_table.setStyle(self.style_efficiency_dqs)
        else:
            data_table.setStyle(table_style_ties)

        table_content = []
        if title_text:
            table_content.append([title])

        if header_text:
            table_content.append([Paragraph(
                f"<i>&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;{header_text}</i>",
                self.text_style_subtitles
            )])

        table_content.append([data_table])

        if footer_text:
            table_content.append([Paragraph(
                f"<i>&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;{footer_text}</i>",
                self.text_style_subtitles
            )])
        if tied_metric:
            tied_metric_footer = self.get_tied_metric_footer(metric_type)
            if tied_metric_footer:
                table_content.append([tied_metric_footer])
        table_with_info = KeepTogether(Table(
            table_content,
            style=TableStyle([("ALIGN", (0, 0), (-1, -1), "CENTER")])
        ))

        return table_with_info

        # elements.append(table_with_info)

    def get_tied_metric_footer(self, metric_type: str) -> Union[Paragraph, None]:

        if metric_type in ["scores", "coaching_efficiency"]:
            if not self.break_ties:
                return Paragraph(self.tie_for_first_footer, self.text_style_normal)
            else:
                return None
        else:
            return Paragraph(self.tie_for_first_footer, self.text_style_normal)

    def create_title(self, title_text: str, title_width: float = 8.5, element_type: str = None, anchor: str = "",
                     subtitle_text: Union[List, str] = None, subsubtitle_text: Union[List, str] = None) -> Table:

        if element_type == "document":
            title_text_style = self.text_style_h1
        elif element_type == "section":
            title_text_style = self.text_style_h2
        elif element_type == "chart":
            title_text_style = self.text_style_invisible
        else:
            title_text_style = self.text_style_h3

        title = Paragraph(f"<para align=center><b>{anchor}{title_text}</b></para>", title_text_style)

        rows = [[title]]

        if subtitle_text:
            if not isinstance(subtitle_text, list):
                subtitle_text = [subtitle_text]

            subtitle_text_str = "<br/>".join(subtitle_text)
            subtitle = Paragraph(f"<para align=center>{subtitle_text_str}</para>", self.text_style_subtitles)
            rows.append([subtitle])

        if subsubtitle_text:
            if not isinstance(subsubtitle_text, list):
                subsubtitle_text = [subsubtitle_text]

            subsubtitle_text_str = "<br/>".join(subsubtitle_text)
            subsubtitle = Paragraph(
                f"<para align=center>{subsubtitle_text_str}</para>", self.text_style_subsubtitles
            )
            rows.append([subsubtitle])

        title_table = Table(rows, colWidths=[title_width * inch] * 1)
        title_table.setStyle(self.title_style)
        return title_table

    def create_anchored_title(self, title_text: str, title_width: float = 8.5, element_type: str = None,
                              anchor: str = "") -> Table:

        if element_type == "document":
            title_text_style = self.text_style_h1
        elif element_type == "section":
            title_text_style = self.text_style_h2
        else:
            title_text_style = self.text_style_h3

        title = Paragraph(f"<para align=center><b>{anchor}{title_text}</b></para>", title_text_style)
        title_table = Table([[title]], colWidths=[title_width * inch] * 1)
        title_table.setStyle(self.title_style)
        return title_table

    def create_data_table(self, metric_type: str, col_headers: List[List[str]], data: Any,
                          table_style: TableStyle = None, table_style_for_ties: TableStyle = None,
                          col_widths: List[float] = None, row_heights: List[List[float]] = None,
                          tied_metric: bool = False) -> Table:

        table_data = deepcopy(col_headers)

        # reduce manager string max characters for standings metric to accommodate narrower column widths
        manager_header_ndx = None
        if metric_type == "standings":
            for header_ndx, header in enumerate(col_headers[0]):
                if header == "Manager":
                    manager_header_ndx = header_ndx

        for row in data:
            display_row = []
            for cell_ndx, cell in enumerate(row):
                if isinstance(cell, str):
                    # truncate data cell contents to specified max characters
                    display_row.append(
                        truncate_cell_for_display(cell, halve_max_chars=(cell_ndx == manager_header_ndx))
                    )
                else:
                    display_row.append(cell)
            table_data.append(display_row)

        if tied_metric:
            if table_data[0][-1] == "Bench Points":
                table = Table(table_data, colWidths=self.widths_06_cols_no_1)
            elif table_data[0][-1] == "Sum % > Avg.":
                table = Table(table_data, colWidths=self.widths_07_cols_no_1)
            else:
                table = Table(table_data, colWidths=col_widths, rowHeights=row_heights)
            table.setStyle(table_style_for_ties)
        else:
            table = Table(table_data, colWidths=col_widths, rowHeights=row_heights)

        if table_style:
            table.setStyle(table_style)
        else:
            table.setStyle(self.style)
        return table

    def create_line_chart(self, data: List[Any], data_length: int, series_names: List[str], chart_title: str,
                          x_axis_title: str, y_axis_title: str, y_step: float) -> LineChartGenerator:

        display_series_names = []
        for name in series_names:
            # truncate series name to specified max characters
            display_series_names.append(truncate_cell_for_display(str(name)))

        series_names = display_series_names

        # see https://sashat.me/2017/01/11/list-of-20-simple-distinct-colors/ for colors
        series_colors = [
            [0, 100, 66, 0, 100],  # red
            [75, 0, 100, 0, 100],  # green
            [0, 25, 95, 0, 100],  # yellow
            [100, 35, 0, 0, 100],  # blue
            [0, 60, 92, 0, 100],  # orange
            [35, 70, 0, 0, 100],  # purple
            [70, 0, 0, 0, 100],  # cyan
            [0, 100, 0, 0, 100],  # magenta
            [35, 0, 100, 0, 100],  # lime
            [0, 30, 15, 0, 100],  # pink
            [100, 0, 0, 50, 100],  # teal
            [10, 25, 0, 0, 100],  # lavender
            [0, 100, 100, 50, 100],  # maroon
            [0, 35, 75, 33, 100],  # brown
            [0, 0, 100, 50, 100],  # olive
            [100, 100, 0, 50, 100],  # navy
            [33, 0, 23, 0, 100],  # mint
            [0, 30, 15, 0, 100],  # pink
            [0, 15, 30, 0, 100],  # apricot
            [5, 10, 30, 0, 100]  # beige
        ]

        # create additional dynamic line chart colors when there are more than 20 teams in a league
        if len(series_names) > len(series_colors):
            additional_team_count = len(series_names) - len(series_colors)

            # ensure that all additional dynamic colors are unique and different from the initial 20 colors
            c_colors = set()
            m_colors = set()
            y_colors = set()
            k_colors = set()
            for color in series_colors:
                c_colors.add(color[0])
                m_colors.add(color[1])
                y_colors.add(color[2])
                k_colors.add(color[3])

            while additional_team_count > 0:
                c_color = choice(list(set(list(range(0, 101))) - c_colors))
                m_color = choice(list(set(list(range(0, 101))) - m_colors))
                y_color = choice(list(set(list(range(0, 101))) - y_colors))
                k_color = choice(list(set(list(range(0, 101))) - k_colors))

                c_colors.add(c_color)
                m_colors.add(m_color)
                y_colors.add(y_color)
                k_colors.add(k_color)

                series_colors.append([c_color, m_color, y_color, k_color, 100])
                additional_team_count -= 1

        box_width = 550
        box_height = 240
        chart_width = 490
        chart_height = 150

        # fit x-axis of table
        x_values_flattened = [weeks[0] for teams in data for weeks in teams]
        x_values_start = x_values_flattened[0] - 1
        x_values_end = x_values_start + data_length + 1
        x_step = 1

        # fit y-axis of table
        y_values_flattened = [weeks[1] if weeks[1] != "DQ" else 0.0 for teams in data for weeks in teams]
        y_values_min = min(y_values_flattened)
        y_values_max = max(y_values_flattened)

        line_chart = LineChartGenerator(
            data,
            self.font,
            self.font_bold,
            chart_title,
            [x_axis_title, x_values_start, x_values_end, x_step],
            [y_axis_title, y_values_min, y_values_max, y_step],
            series_names,
            series_colors,
            box_width,
            box_height,
            chart_width,
            chart_height
        )

        return line_chart

    def create_3d_horizontal_bar_chart(self, data: List[List[Any]], x_axis_title: str, x_step: int):

        data = [[team[0], team[1], team[2], int(team[3])] for team in data]

        box_width = 425
        box_height = 425
        chart_width = 425
        chart_height = 425

        horizontal_bar_chart = HorizontalBarChart3DGenerator(
            data,
            self.font,
            self.font_size,
            [x_axis_title, 0, max([team[3] for team in data]) + 1, x_step],
            box_width,
            box_height,
            chart_width,
            chart_height
        )

        return horizontal_bar_chart

    @staticmethod
    def get_img(path: Union[Path, str], width: int = 1 * inch, hyperlink: str = None) -> ReportLabImage:
        img = ImageReader(path)
        iw, ih = img.getSize()
        aspect = ih / float(iw)

        if hyperlink:
            image = HyperlinkedImage(
                path,
                hyperlink=hyperlink,
                width=width,
                height=(width * aspect)
            )
        else:
            image = ReportLabImage(path, width=width, height=(width * aspect))

        return image

    def create_team_stats_pages(self, doc_elements: List[Flowable], weekly_team_data_by_position: List[List[Any]],
                                season_average_team_data_by_position: Dict[str, List[List[float]]]):
        logger.debug("Creating team stats pages.")

        # reorganize weekly_team_data_by_position to alphabetical order by team name
        alphabetical_teams = []
        for team in sorted(self.teams_results.values(), key=lambda x: x.name):
            for team_data in weekly_team_data_by_position:
                if team.team_id == team_data[0]:
                    alphabetical_teams.append(team_data)

        for team in alphabetical_teams:
            team_id = team[0]
            team_weekly_points_by_position = team[1]
            team_result: BaseTeam = self.teams_results[team_id]
            player_info = team_result.roster

            if (settings.report_settings.team_points_by_position_charts_bool
                    or settings.report_settings.team_bad_boy_stats_bool
                    or settings.report_settings.team_beef_stats_bool
                    or settings.report_settings.team_boom_or_bust_bool):
                title = self.create_title("<i>" + team_result.name + "</i>", element_type="section",
                                          anchor="<a name = page.html#" + str(self.toc.get_current_anchor()) + "></a>")
                self.toc.add_team_section(team_result.name)

                doc_elements.append(title)

            if settings.report_settings.team_points_by_position_charts_bool:
                labels = []
                weekly_data = []
                season_data = [x[1] for x in season_average_team_data_by_position.get(team_id)]
                for week in team_weekly_points_by_position:
                    labels.append(week[0])
                    weekly_data.append(week[1])

                team_table = Table(
                    [[self.create_title("Weekly Points by Position", title_width=2.00),
                      self.create_title("Season Average Points by Position", title_width=2.00)],
                     [BreakdownPieDrawing(labels, weekly_data, font=self.font),
                      BreakdownPieDrawing(labels, season_data, font=self.font)]],
                    colWidths=[4.25 * inch, 4.25 * inch],
                    style=TableStyle([
                        ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.white),
                        ("BOX", (0, 0), (-1, -1), 0.25, colors.white),
                        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                        ("VALIGN", (0, 0), (-1, 0), "MIDDLE")
                    ]))
                doc_elements.append(KeepTogether(team_table))
                doc_elements.append(self.spacer_quarter_inch)

            if (settings.report_settings.league_bad_boy_rankings_bool
                    and settings.report_settings.team_bad_boy_stats_bool):

                if player_info:
                    offending_players = []
                    for player in player_info:
                        if player.bad_boy_points > 0:
                            offending_players.append(player)

                    offending_players = sorted(offending_players, key=lambda x: x.bad_boy_points, reverse=True)
                    offending_players_data = []
                    for player in offending_players:
                        offending_players_data.append([player.full_name, player.bad_boy_points, player.bad_boy_crime])
                    # if there are no offending players, skip table
                    if offending_players_data:
                        doc_elements.append(self.create_title("Whodunnit?", 8.5, "section"))
                        doc_elements.append(self.spacer_tenth_inch)
                        bad_boys_table = self.create_data_table(
                            "bad_boy",
                            [["Starting Player", "Bad Boy Points", "Worst Offense"]],
                            offending_players_data,
                            self.style_red_highlight,
                            self.style_tied_bad_boy,
                            [2.50 * inch, 2.50 * inch, 2.75 * inch]
                        )
                        doc_elements.append(KeepTogether(bad_boys_table))
                        doc_elements.append(self.spacer_tenth_inch)

            if (settings.report_settings.league_beef_rankings_bool
                    and settings.report_settings.team_beef_stats_bool):

                if player_info:
                    doc_elements.append(self.create_title("Beefiest Bois", 8.5, "section"))
                    doc_elements.append(self.spacer_tenth_inch)
                    beefy_players = sorted(
                        [player for player in player_info if player.primary_position != "D/ST"],
                        key=lambda x: x.tabbu, reverse=True
                    )
                    beefy_players_data = []
                    num_beefy_bois = 3
                    ndx = 0
                    count = 0
                    while count < num_beefy_bois:
                        player: BasePlayer = beefy_players[ndx]
                        if player.last_name:
                            beefy_players_data.append([player.full_name, f"{player.tabbu:.3f}", player.weight])
                            count += 1
                        ndx += 1
                    beefy_boi_table = self.create_data_table(
                        "beef",
                        [["Starting Player", "TABBU(s)", "Weight (lbs.)"]],
                        beefy_players_data,
                        self.style_red_highlight,
                        self.style_tied_bad_boy,
                        [2.50 * inch, 2.50 * inch, 2.75 * inch]
                    )
                    doc_elements.append(KeepTogether(beefy_boi_table))
                    doc_elements.append(self.spacer_tenth_inch)

            if settings.report_settings.team_boom_or_bust_bool:
                if player_info:
                    starting_players = []
                    player: BasePlayer
                    for player in player_info:
                        if player.selected_position not in self.report_data.bench_positions:
                            if player.season_points and player.week_for_report > 1:
                                player.season_average_points = round(
                                    (player.season_points - player.points) / (player.week_for_report - 1), 2)
                                player.season_average_points = round(
                                    (player.season_points - player.points -
                                     ((self.report_data.week - player.week_for_report - 1) *
                                      player.season_average_points)) / (player.week_for_report - 1), 2)
                            else:
                                player.season_points = 0
                                player.season_average_points = 0
                            starting_players.append(player)

                    if (any(player.season_points for player in starting_players)
                            and starting_players[0].week_for_report > 1):

                        starting_players = sorted(
                            starting_players,
                            key=lambda x:
                            round(((x.points - x.season_average_points) / x.season_average_points) * 100, 2)
                            if x.season_average_points > 0
                            else 100
                            if x.points > 0
                            else 0
                            if x.points == 0
                            else round(((x.points - x.season_average_points) / x.season_average_points) * -100, 2)
                            if x.season_average_points > 0
                            else -100,
                            reverse=True
                        )
                    else:
                        starting_players = sorted(starting_players, key=lambda x: x.points, reverse=True)

                    best_weekly_player = starting_players[0]
                    worst_weekly_player = starting_players[-1]

                    best_player_headshot = get_player_image(
                        best_weekly_player.headshot_url, self.data_dir, self.week_for_report,
                        settings.report_settings.image_quality, 1.5 * inch, best_weekly_player.full_name,
                        self.report_data.league.offline
                    )
                    worst_player_headshot = get_player_image(
                        worst_weekly_player.headshot_url, self.data_dir, self.week_for_report,
                        settings.report_settings.image_quality, 1.5 * inch, worst_weekly_player.full_name,
                        self.report_data.league.offline
                    )

                    boom_title = choice([
                        "BOOOOOOOOM", "Certified Stud", "Cash Money", "To the Moon!",
                        "The King", "O Captain! My Captain!", "STILL HUNGRY...", "Haters gonna hate!",
                        "Price just went up!", "Future HOFer", "Put Da Team On My Back", "Can't Hold Me Down",
                        "Unstoppable Force", "Immovable Object", "GOAT", "Showed Up and Showed Out"
                    ])
                    bust_title = choice([
                        "...b... U... s... T", "Better luck next year...", "OUCH...!", "...took an arrow in the knee",
                        "Future Benchwarmer", "Needs Grip Boost!", "Underachievers Anonymous", "MIA",
                        "Pennies on the Dollar", "Stoppable Force", "Movable Object", "Over-promise, Under-deliver!",
                        "La La La I Can't Hear You", "Losing Builds Character!", "Better hit the waiver wire!", "DUD"
                    ])
                    data = [
                        [boom_title, bust_title],
                        [best_weekly_player.full_name + " -- " + (best_weekly_player.nfl_team_name if
                                                                  best_weekly_player.nfl_team_name else "N/A"),
                         worst_weekly_player.full_name + " -- " + (worst_weekly_player.nfl_team_name if
                                                                   worst_weekly_player.nfl_team_name else "N/A")],
                        [best_player_headshot, worst_player_headshot]
                    ]
                    if (any(player.season_points for player in starting_players)
                            and starting_players[0].week_for_report > 1):

                        if best_weekly_player.season_average_points > 0:
                            boom_pct_above_avg = round(
                                ((best_weekly_player.points - best_weekly_player.season_average_points)
                                 / best_weekly_player.season_average_points) * 100, 2
                            )
                        elif best_weekly_player.season_average_points == 0:
                            boom_pct_above_avg = "∞"
                        else:
                            boom_pct_above_avg = round(
                                ((best_weekly_player.season_average_points - best_weekly_player.points)
                                 / best_weekly_player.season_average_points) * -100, 2
                            )

                        if worst_weekly_player.season_average_points > 0:
                            bust_pct_above_avg = round(
                                ((worst_weekly_player.season_average_points - worst_weekly_player.points)
                                 / worst_weekly_player.season_average_points) * 100, 2
                            )
                        elif worst_weekly_player.season_average_points == 0:
                            bust_pct_above_avg = "∞"
                        else:
                            bust_pct_above_avg = round(
                                ((worst_weekly_player.season_average_points - worst_weekly_player.points)
                                 / worst_weekly_player.season_average_points) * -100, 2
                            )

                        data.append(
                            [
                                (f"{round(best_weekly_player.points, 2)} "
                                 f"({best_weekly_player.season_average_points} avg: +{boom_pct_above_avg}%)"),
                                (f"{round(worst_weekly_player.points, 2)} "
                                 f"({worst_weekly_player.season_average_points} avg: -{bust_pct_above_avg}%)")
                            ]
                        )
                    else:
                        data.append([round(best_weekly_player.points, 2), round(worst_weekly_player.points, 2)])

                    table = Table(data, colWidths=4.0 * inch)
                    table.setStyle(self.boom_bust_table_style)
                    doc_elements.append(self.spacer_half_inch)
                    doc_elements.append(self.create_title("Boom... or Bust", 8.5, "section"))
                    doc_elements.append(self.spacer_tenth_inch)
                    doc_elements.append(KeepTogether(table))

                if (settings.report_settings.team_points_by_position_charts_bool
                        or settings.report_settings.team_bad_boy_stats_bool
                        or settings.report_settings.team_beef_stats_bool
                        or settings.report_settings.team_boom_or_bust_bool):
                    doc_elements.append(self.add_page_break())

    def generate_pdf(self, filename_with_path: Path, line_chart_data_list: List[List[Any]]) -> str:
        logger.debug("Generating report PDF.")

        elements: List[Flowable] = []
        # doc = SimpleDocTemplate(filename_with_path, pagesize=LETTER, rightMargin=25, leftMargin=25, topMargin=10,
        #                         bottomMargin=10)
        doc = SimpleDocTemplate(str(filename_with_path), pagesize=LETTER, rightMargin=25, leftMargin=25, topMargin=10,
                                bottomMargin=10, initialFontName=self.font, initialFontSize=12)
        doc.pagesize = portrait(LETTER)

        # document title
        elements.append(self.report_title)
        elements.append(self.spacer_tenth_inch)
        donate_header_data = [[
            Paragraph(
                "Enjoying the app? Please consider donating to support its development:", self.text_style_italics),
            self.get_img(
                "resources/images/donate.png",
                hyperlink="https://www.paypal.com/cgi-bin/webscr?cmd=_donations&business=VZZCNLRHH9BQS"
            )
        ]]
        elements.append(Table(donate_header_data, colWidths=[4.65 * inch, 1.00 * inch], style=self.header_style))
        elements.append(self.spacer_tenth_inch)

        elements.append(self.add_page_break())

        # standings
        if settings.report_settings.league_standings_bool:
            # update standings style to vertically justify all rows
            standings_style = deepcopy(self.style)
            standings_style.add("VALIGN", (0, 0), (-1, -1), "MIDDLE")

            if self.report_data.has_divisions:
                self.standings_headers[0].insert(4, "Division")
                original_font_size = deepcopy(self.font_size)
                self.font_size -= 2

                division_standings_list = []
                division_count = 1

                for division in self.report_data.data_for_current_division_standings:

                    if division_count == 1:
                        table_title = "League Standings"
                        table_footer = None
                    elif division_count == len(self.report_data.data_for_current_division_standings):
                        table_title = None
                        table_footer = "† Division Leaders"
                    else:
                        table_title = None
                        table_footer = None

                    table_header = (
                        self.report_data.divisions[division[0][-1]]
                        if self.report_data.divisions else f"Division {division_count}"
                    )

                    division_table = self.create_section(
                        table_title,
                        self.standings_headers,
                        [team[:-1] for team in division],
                        standings_style,
                        standings_style,
                        self.widths_11_cols_no_1,
                        header_text=table_header,
                        footer_text=table_footer,
                        metric_type="standings"
                    )

                    division_standings_list.append(division_table)
                    division_standings_list.append(self.spacer_tenth_inch)
                    division_count += 1

                self.font_size = original_font_size
                standings = KeepTogether(division_standings_list)

            else:
                standings = self.create_section(
                    "League Standings",
                    self.standings_headers,
                    self.report_data.data_for_current_standings,
                    standings_style,
                    standings_style,
                    self.widths_10_cols_no_1,
                    metric_type="standings"
                )
            elements.append(standings)
            elements.append(self.spacer_tenth_inch)

        if settings.report_settings.league_playoff_probs_bool and self.playoff_slots > 0:
            # update playoff probabilities style to make playoff teams green
            playoff_probs_style = deepcopy(self.style)
            playoff_probs_style.add("TEXTCOLOR", (0, 1), (-1, self.playoff_slots), colors.green)
            playoff_probs_style.add("FONT", (0, 1), (-1, -1), self.font)
            if self.report_data.has_divisions:
                self.playoff_probs_headers[0].insert(3, "Division")
                playoff_probs_style.add("FONTSIZE", (0, 0), (-1, -1), self.font_size - 4)
                self.widths_n_cols_no_1 = (
                        [1.35 * inch, 0.90 * inch, 0.75 * inch, 0.75 * inch, 0.50 * inch, 0.50 * inch] +
                        [round(3.4 / self.playoff_slots, 2) * inch] * self.playoff_slots
                )

            data_for_playoff_probs = self.report_data.data_for_playoff_probs
            team_num = 1
            if data_for_playoff_probs:
                for team in data_for_playoff_probs:
                    prob_ndx = 3
                    if self.report_data.has_divisions:
                        prob_ndx = 4
                    # if float(team[prob_ndx].split("%")[0]) == 100.00 and int(team[prob_ndx + 1].split(" ")[0]) == 0:
                    if float(team[prob_ndx].split("%")[0]) == 100.00:
                        playoff_probs_style.add("TEXTCOLOR", (0, team_num), (-1, team_num), colors.darkgreen)
                        playoff_probs_style.add("FONT", (0, team_num), (-1, team_num), self.font_bold_italic)

                    if ((int(team[prob_ndx + 1].split(" ")[0]) + int(
                            self.week_for_report)) > self.num_regular_season_weeks) or (
                            float(team[prob_ndx].split("%")[0]) == 0.00):
                        playoff_probs_style.add(
                            "TEXTCOLOR", (prob_ndx + 1, team_num), (prob_ndx + 1, team_num), colors.red)

                        if float(team[prob_ndx].split("%")[0]) == 0.00:
                            playoff_probs_style.add("TEXTCOLOR", (0, team_num), (-1, team_num), colors.darkred)
                            playoff_probs_style.add("FONT", (0, team_num), (-1, team_num), self.font_bold_italic)

                    team_num += 1

            # playoff probabilities
            if data_for_playoff_probs:
                num_playoff_simulations = (
                    int(self.playoff_prob_sims)
                    if self.playoff_prob_sims is not None
                    else settings.num_playoff_simulations
                )

                elements.append(self.create_section(
                    "Playoff Probabilities",
                    self.playoff_probs_headers,
                    data_for_playoff_probs,
                    playoff_probs_style,
                    playoff_probs_style,
                    self.widths_n_cols_no_1,
                    subtitle_text=(
                            f"Playoff probabilities were calculated using {num_playoff_simulations:,} Monte Carlo "
                            f"simulations to predict team performances through the end of the regular fantasy season."
                            + (
                                "\nProbabilities account for division winners in addition to overall win/loss/tie record."
                                if self.report_data.has_divisions else "")
                    ),
                    metric_type="playoffs",
                    footer_text=(
                        f"""† Predicted Division Leaders{
                        "<br></br>&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;"
                        "‡ Predicted Division Qualifiers"
                        if settings.num_playoff_slots_per_division > 1
                        else ""
                        }"""
                        if self.report_data.has_divisions else None
                    )
                ))

        if settings.report_settings.league_standings_bool or settings.report_settings.league_playoff_probs_bool:
            elements.append(self.add_page_break())

        if settings.report_settings.league_median_standings_bool:
            # update median standings style to italicize ranking column
            median_standings_style = deepcopy(self.style)
            median_standings_style.add("FONT", (3, 1), (3, -1), self.font_italic)
            median_standings_style.add("FONTSIZE", (0, 0), (-1, -1), self.font_size - 4)

            # median standings
            elements.append(self.create_section(
                "League Median Matchup Standings",
                self.median_standings_headers,
                [team[:-1] for team in self.data_for_median_standings],
                median_standings_style,
                None,
                self.widths_07_cols_no_2,
                metric_type="median_standings",
                subtitle_text="League standings when every team plays against the league median score each week.<br/>",
                subsubtitle_text=(
                    f"WEEK {self.week_for_report} LEAGUE MEDIAN SCORE: {self.data_for_median_standings[0][-1]}"
                )
            ))
            elements.append(self.spacer_twentieth_inch)
            elements.append(self.add_page_break())

        if settings.report_settings.league_power_rankings_bool:
            # power ranking
            elements.append(self.create_section(
                "Team Power Rankings",
                self.power_ranking_headers,
                self.data_for_power_rankings,
                self.style,
                self.style_tied_power_rankings,
                self.widths_04_cols_no_1,
                tied_metric=self.report_data.ties_for_power_rankings > 0,
                metric_type="power_ranking",
                subtitle_text="Average of weekly score, coaching efficiency and luck ranks."
            ))
            elements.append(self.spacer_twentieth_inch)

        if settings.report_settings.league_z_score_rankings_bool:
            # z-scores (if week 3 or later, once meaningful z-scores can be calculated)
            if self.data_for_z_scores:
                elements.append(self.create_section(
                    "Team Z-Score Rankings",
                    self.zscores_headers,
                    self.data_for_z_scores,
                    self.style,
                    None,
                    self.widths_04_cols_no_1,
                    tied_metric=False,
                    metric_type="z_score",
                    subtitle_text=[
                        "Measure of standard deviations away from mean for a score. Shows teams performing ",
                        "above or below their normal scores for the current week.  See <a href = "
                        "'https://en.wikipedia.org/wiki/Standard_score' color='blue'>Standard Score</a>."
                    ]
                ))

        if settings.report_settings.league_power_rankings_bool or settings.report_settings.league_z_score_rankings_bool:
            elements.append(self.add_page_break())

        if settings.report_settings.league_score_rankings_bool:
            # scores
            elements.append(self.create_section(
                "Team Score Rankings",
                self.scores_headers,
                self.data_for_scores,
                self.style,
                self.style_tied_scores,
                self.widths_05_cols_no_1,
                tied_metric=self.report_data.ties_for_scores > 0,
                metric_type="scores"
            ))
            elements.append(self.spacer_twentieth_inch)

        if settings.report_settings.league_coaching_efficiency_rankings_bool:
            # coaching efficiency
            elements.append(self.create_section(
                "Team Coaching Efficiency Rankings",
                self.efficiency_headers,
                self.data_for_coaching_efficiency,
                self.style,
                self.style_tied_efficiencies,
                self.widths_05_cols_no_1,
                tied_metric=((self.report_data.ties_for_coaching_efficiency > 0) and
                             (self.report_data.league.player_data_by_week_function is not None)),
                metric_type="coaching_efficiency"
            ))
            elements.append(self.spacer_twentieth_inch)

        if settings.report_settings.league_luck_rankings_bool:
            # luck
            elements.append(self.create_section(
                "Team Luck Rankings",
                self.luck_headers,
                self.data_for_luck,
                self.style,
                self.style_tied_luck,
                # self.widths_5_cols_1,
                self.widths_06_cols_no_3,
                tied_metric=self.report_data.ties_for_luck > 0,
                metric_type="luck"
            ))

        if (settings.report_settings.league_score_rankings_bool
                or settings.report_settings.league_coaching_efficiency_rankings_bool
                or settings.report_settings.league_luck_rankings_bool):
            elements.append(self.add_page_break())

        if settings.report_settings.league_optimal_score_rankings_bool:
            # optimal scores
            elements.append(self.create_section(
                "Team Optimal Score Rankings",
                self.optimal_scores_headers,
                self.data_for_optimal_scores,
                self.style,
                self.style,
                self.widths_05_cols_no_1
            ))
            elements.append(self.spacer_twentieth_inch)
            elements.append(self.add_page_break())

        if settings.report_settings.league_bad_boy_rankings_bool:
            # bad boy rankings
            elements.append(self.create_section(
                "Bad Boy Rankings",
                self.bad_boy_headers,
                self.data_for_bad_boy_rankings,
                self.style,
                self.style_tied_bad_boy,
                self.widths_06_cols_no_2,
                tied_metric=self.report_data.ties_for_bad_boy_rankings > 0,
                metric_type="bad_boy"
            ))
            elements.append(self.spacer_twentieth_inch)

        if settings.report_settings.league_beef_rankings_bool:
            # beef rankings
            elements.append(self.create_section(
                "Beef Rankings",
                self.beef_headers,
                self.data_for_beef_rankings,
                self.style_left_align_right_col,
                self.style_tied_beef,
                self.widths_04_cols_no_2,
                tied_metric=self.report_data.ties_for_beef_rankings > 0,
                metric_type="beef",
                subtitle_text=[
                    "Team Beef Ranking is measured in TABBUs (Trimmed And Boneless Beef Units). "
                    "One TABBU is currently established as 500 lbs.",
                    "TABBU derivation stems from academic research done for the beef industry found <a href = "
                    "'https://extension.tennessee.edu/publications/Documents/PB1822.pdf' color='blue'>here</a>."
                ]
            ))

        if settings.report_settings.league_bad_boy_rankings_bool or settings.report_settings.league_beef_rankings_bool:
            elements.append(self.add_page_break())

        if settings.report_settings.league_weekly_top_scorers_bool:
            weekly_top_scorers_title_str = "Weekly Top Scorers"
            weekly_top_scorers_page_title = self.create_title(
                "<i>" + weekly_top_scorers_title_str + "</i>", element_type="chart",
                anchor="<a name = page.html#" + str(self.toc.get_current_anchor()) + "></a>")
            elements.append(weekly_top_scorers_page_title)

            # weekly top scorers
            elements.append(self.create_section(
                "Weekly Top Scorers",
                self.weekly_top_scorer_headers,
                self.data_for_season_weekly_top_scorers,
                self.style_no_highlight,
                self.style_no_highlight,
                self.widths_04_cols_no_1,
                metric_type="top_scorers",
                section_title_function=self.toc.add_top_performers_section
            ))
            elements.append(self.spacer_twentieth_inch)

        if settings.report_settings.league_weekly_highest_ce_bool:
            weekly_highest_ce_title_str = "Weekly Highest Coaching Efficiency"
            weekly_highest_ce_page_title = self.create_title(
                "<i>" + weekly_highest_ce_title_str + "</i>", element_type="chart",
                anchor="<a name = page.html#" + str(self.toc.get_current_anchor()) + "></a>")
            elements.append(weekly_highest_ce_page_title)

            # weekly highest coaching efficiency
            elements.append(self.create_section(
                "Weekly Highest Coaching Efficiency",
                self.weekly_highest_ce_headers,
                self.data_for_season_weekly_highest_ce,
                self.style_no_highlight,
                self.style_no_highlight,
                self.widths_04_cols_no_1,
                metric_type="highest_ce",
                section_title_function=self.toc.add_top_performers_section
            ))

        if (settings.report_settings.league_weekly_top_scorers_bool
                or settings.report_settings.league_weekly_highest_ce_bool):
            elements.append(self.add_page_break())

        if settings.report_settings.report_time_series_charts_bool:
            series_names = line_chart_data_list[0]
            points_data = line_chart_data_list[2]
            efficiency_data = line_chart_data_list[3]
            luck_data = line_chart_data_list[4]

            # Remove any zeros from coaching efficiency to make table prettier
            for team in efficiency_data:
                week_index = 0
                for week in team:
                    if len(team) > 1:
                        if week[1] == 0.0:
                            del team[week_index]

                    if week[1] == "DQ":
                        week[1] = 0.0

                    week_index += 1

            # create line charts for points, coaching efficiency, and luck
            charts_time_series_page_title_str = "Time Series Charts"
            charts_time_series_page_title = self.create_title(
                "<i>" + charts_time_series_page_title_str + "</i>", element_type="chart",
                anchor="<a name = page.html#" + str(self.toc.get_current_anchor()) + "></a>")
            self.toc.add_chart_section(charts_time_series_page_title_str)
            elements.append(charts_time_series_page_title)
            elements.append(KeepTogether(
                self.create_line_chart(points_data, len(points_data[0]), series_names, "Weekly Points", "Weeks",
                                       "Fantasy Points", 10.00)))
            elements.append(self.spacer_twentieth_inch)
            # NOTE: MUST USE POINTS DATA FOR COACHING EFFICIENCY DATA LENGTH ARGUMENT IN THE EVENT OF DQs
            elements.append(KeepTogether(
                self.create_line_chart(efficiency_data, len(points_data[0]), series_names, "Weekly Coaching Efficiency",
                                       "Weeks", "Coaching Efficiency (%)", 10.00)))
            elements.append(self.spacer_twentieth_inch)
            elements.append(KeepTogether(
                self.create_line_chart(luck_data, len(luck_data[0]), series_names, "Weekly Luck", "Weeks", "Luck (%)",
                                       20.00)))
            elements.append(self.spacer_tenth_inch)
            elements.append(self.add_page_break())

        if (settings.report_settings.report_team_stats_bool
                and settings.report_settings.team_points_by_position_charts_bool
                and settings.report_settings.team_bad_boy_stats_bool
                and settings.report_settings.team_beef_stats_bool
                and settings.report_settings.team_boom_or_bust_bool):
            # dynamically build additional pages for individual team stats
            self.create_team_stats_pages(
                elements, self.data_for_weekly_points_by_position, self.data_for_season_average_team_points_by_position
            )

        # add appendix for metrics
        elements.append(self.appendix.get_appendix())
        elements.append(self.add_page_break())
        self.toc.add_appendix("Appendix I: Rankings & Metrics")

        # insert table of contents after report title and spacer
        elements.insert(4, self.toc.get_toc())

        elements.append(self.report_footer_title)
        elements.append(self.report_footer)

        # build pdf
        logger.info(f"generating PDF ({str(filename_with_path).split('/')[-1]})...")
        # doc.build(elements, onFirstPage=self.add_page_number, onLaterPages=self.add_page_number)
        doc.build(elements, onLaterPages=self.add_page_number)

        return doc.filename


class TableOfContents(object):

    def __init__(self, font, font_size, break_ties):

        self.break_ties = break_ties

        self.toc_style_right = ParagraphStyle(name="tocr", alignment=TA_RIGHT, fontSize=font_size - 2, fontName=font)
        self.toc_style_center = ParagraphStyle(name="tocc", alignment=TA_CENTER, fontSize=font_size - 2, fontName=font)
        self.toc_style_left = ParagraphStyle(name="tocl", alignment=TA_LEFT, fontSize=font_size - 2, fontName=font)
        self.toc_style_title_right = ParagraphStyle(name="tocr", alignment=TA_RIGHT, fontSize=font_size,
                                                    fontName=font)
        self.toc_style_title_left = ParagraphStyle(name="tocl", alignment=TA_LEFT, fontSize=font_size,
                                                   fontName=font)

        self.toc_anchor = 0

        # start on page 1 since table of contents is on first page
        self.toc_page = 1

        self.toc_metric_section_data = None
        self.toc_top_performers_section_data = None
        self.toc_chart_section_data = None
        self.toc_team_section_data = None
        self.toc_appendix_data = None

        if (settings.report_settings.league_standings_bool
                or settings.report_settings.league_playoff_probs_bool
                or settings.report_settings.league_median_standings_bool
                or settings.report_settings.league_power_rankings_bool
                or settings.report_settings.league_z_score_rankings_bool
                or settings.report_settings.league_score_rankings_bool
                or settings.report_settings.league_coaching_efficiency_rankings_bool
                or settings.report_settings.league_luck_rankings_bool
                or settings.report_settings.league_optimal_score_rankings_bool
                or settings.report_settings.league_bad_boy_rankings_bool
                or settings.report_settings.league_beef_rankings_bool):
            self.toc_metric_section_data = [
                [Paragraph("<b><i>Metrics</i></b>", self.toc_style_title_right),
                 "",
                 Paragraph("<b><i>Page</i></b>", self.toc_style_title_left)]
            ]
            self.toc_appendix_data = [
                [Paragraph("<b><i>Appendices</i></b>", self.toc_style_title_right),
                 "",
                 Paragraph("<b><i>Page</i></b>", self.toc_style_title_left)]
            ]

        if (settings.report_settings.league_weekly_top_scorers_bool
                or settings.report_settings.league_weekly_highest_ce_bool):
            self.toc_top_performers_section_data = [
                [Paragraph("<b><i>Top Performers</i></b>", self.toc_style_title_right),
                 "",
                 Paragraph("<b><i>Page</i></b>", self.toc_style_title_left)]
            ]

        if settings.report_settings.report_time_series_charts_bool:
            self.toc_chart_section_data = [
                [Paragraph("<b><i>Charts</i></b>", self.toc_style_title_right),
                 "",
                 Paragraph("<b><i>Page</i></b>", self.toc_style_title_left)]
            ]

        if (settings.report_settings.report_team_stats_bool
                and (
                        settings.report_settings.team_points_by_position_charts_bool
                        or settings.report_settings.team_bad_boy_stats_bool
                        or settings.report_settings.team_beef_stats_bool
                        or settings.report_settings.team_boom_or_bust_bool
                )):
            self.toc_team_section_data = [
                [Paragraph("<b><i>Teams</i></b>", self.toc_style_title_right),
                 "",
                 Paragraph("<b><i>Page</i></b>", self.toc_style_title_left)]
            ]

    def add_toc_page(self, pages_to_add=1):
        self.toc_page += pages_to_add

    def format_toc_section(self, title, color="blue"):
        return [
            Paragraph(
                "<a href = #page.html#" + str(self.toc_anchor) + " color=" + color + "><b><u>" + title + "</u></b></a>",
                self.toc_style_right),
            Paragraph(". . . . . . . . . . . . . . . . . . . .", self.toc_style_center),
            Paragraph(str(self.toc_page), self.toc_style_left)
        ]

    def add_metric_section(self, title):
        if self.break_ties:
            if title == "Team Score Rankings" or title == "Team Coaching Efficiency Rankings":
                color = "green"
            else:
                color = "blue"
        else:
            color = "blue"
        metric_section = self.format_toc_section(title, color)
        self.toc_metric_section_data.append(metric_section)
        self.toc_anchor += 1

    def add_top_performers_section(self, title):
        top_performers_section = self.format_toc_section(title)
        self.toc_top_performers_section_data.append(top_performers_section)
        self.toc_anchor += 1

    def add_chart_section(self, title):
        chart_section = self.format_toc_section(title)
        self.toc_chart_section_data.append(chart_section)
        self.toc_anchor += 1

    def add_team_section(self, team_name):
        team_section = self.format_toc_section(team_name)
        self.toc_team_section_data.append(team_section)
        self.toc_anchor += 1

    def add_appendix(self, title):
        appendix_section = self.format_toc_section(title)
        self.toc_appendix_data.append(appendix_section)
        self.toc_anchor += 1

    def get_current_anchor(self):
        return self.toc_anchor

    # noinspection DuplicatedCode
    def get_toc(self):

        row_heights = []
        if self.toc_metric_section_data:
            row_heights.extend([0.25 * inch] * len(self.toc_metric_section_data))
            row_heights.append(0.05 * inch)
        if self.toc_top_performers_section_data:
            row_heights.extend([0.25 * inch] * len(self.toc_top_performers_section_data))
            row_heights.append(0.05 * inch)
        if self.toc_chart_section_data:
            row_heights.extend([0.25 * inch] * len(self.toc_chart_section_data))
            row_heights.append(0.05 * inch)
        if self.toc_team_section_data:
            row_heights.extend([0.25 * inch] * len(self.toc_team_section_data))
            row_heights.append(0.05 * inch)
        if self.toc_appendix_data:
            row_heights.extend([0.25 * inch] * len(self.toc_appendix_data))

        return Table(
            (self.toc_metric_section_data + [["", "", ""]] if self.toc_metric_section_data else []) +
            (self.toc_top_performers_section_data + [["", "", ""]] if self.toc_top_performers_section_data else []) +
            (self.toc_chart_section_data + [["", "", ""]] if self.toc_chart_section_data else []) +
            (self.toc_team_section_data + [["", "", ""]] if self.toc_team_section_data else []) +
            (self.toc_appendix_data if self.toc_appendix_data else []),
            colWidths=[3.25 * inch, 2 * inch, 2.50 * inch],
            rowHeights=row_heights
        )


class Appendix(object):

    def __init__(self, title, title_formatter, toc_anchor_getter, font_size, style):
        self.title = title
        self.title_formatter = title_formatter
        self.toc_anchor_getter = toc_anchor_getter
        self.font_size = font_size
        self.style = style
        self.entries = []
        self.entry_anchor_num = 1000

    def get_last_entry_anchor(self):
        return str(self.entry_anchor_num - 1)

    def add_entry(self, title, section_anchor, text):
        body_style: ParagraphStyle = deepcopy(self.style)
        body_style.fontSize = self.font_size - 4
        body_style.firstLineIndent = 1
        entry = Paragraph(
            '''<para align=left>''' +
            '''<a name = page.html#''' + str(self.entry_anchor_num) + '''></a>''' +
            '''<a href = #page.html#''' + section_anchor +
            ''' color=blue><b><u>''' +
            title +
            '''</u></b></a><br/></para><para>&nbsp;&nbsp;&nbsp;&nbsp;''' +
            text +
            '''<br/></para>''',
            body_style
        )
        self.entry_anchor_num += 1
        self.entries.append([entry])

    def get_appendix(self):
        title = self.title_formatter(
            self.title,
            title_width=7.75,
            element_type="section",
            anchor="<a name = page.html#" + str(self.toc_anchor_getter()) + "></a>"
        )
        self.entries.insert(0, [title])
        return Table(
            self.entries,
            colWidths=[7.75 * inch]
        )
