"""编译层页面格式：解析、序列化、段落定义（spec 设计要点 2）。"""
from .format import (CITATION, KP_SECTIONS, PAGE_TYPES, QUOTE_SLOT, RESERVED_PAGE_TYPES, SECTIONS_BY_TYPE, WIKILINK, AtomRef,
                     Page, PageFormatError, Section, SectionSpec, paragraphs, parse, serialize)

__all__ = ["CITATION", "KP_SECTIONS", "PAGE_TYPES", "QUOTE_SLOT", "RESERVED_PAGE_TYPES", "SECTIONS_BY_TYPE", "WIKILINK", "AtomRef",
           "Page", "PageFormatError", "Section", "SectionSpec", "paragraphs", "parse", "serialize"]
