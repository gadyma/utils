import zipfile
import xml.etree.ElementTree as ET
import sys
import os

def parse_element(elem, ns):
    """
    פונקציה רקורסיבית העוברת על עץ ה-XML של המסמך ומחלצת טקסט, 
    מעקב שינויים וסימוני הערות.
    """
    text_parts = []
    tag = elem.tag
    
    if tag == f"{{{ns['w']}}}commentRangeStart":
        c_id = elem.attrib.get(f"{{{ns['w']}}}id")
        text_parts.append(f" <<תחילת הערה {c_id}>> ")
    elif tag == f"{{{ns['w']}}}commentRangeEnd":
        c_id = elem.attrib.get(f"{{{ns['w']}}}id")
        text_parts.append(f" <<סיום הערה {c_id}>> ")
    elif tag == f"{{{ns['w']}}}ins":
        inner_text = ""
        for child in elem:
            inner_text += parse_element(child, ns)
        if inner_text:
            text_parts.append(f"[הוספה: {inner_text}]")
    elif tag == f"{{{ns['w']}}}del":
        inner_text = ""
        for child in elem:
            inner_text += parse_element(child, ns)
        if inner_text:
            text_parts.append(f"[מחיקה: {inner_text}]")
    elif tag in (f"{{{ns['w']}}}t", f"{{{ns['w']}}}delText"):
        if elem.text:
            text_parts.append(elem.text)
    elif tag == f"{{{ns['w']}}}p":
        # ירידת שורה בסוף כל פסקה
        for child in elem:
            text_parts.append(parse_element(child, ns))
        text_parts.append("\n\n")
    else:
        for child in elem:
            text_parts.append(parse_element(child, ns))
            
    return "".join(text_parts)

def extract_full_context(docx_path):
    ns = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
    
    try:
        with zipfile.ZipFile(docx_path, 'r') as docx:
            comments_xml = docx.read('word/comments.xml') if 'word/comments.xml' in docx.namelist() else None
            document_xml = docx.read('word/document.xml')
    except Exception as e:
        print(f"שגיאה בפתיחת הקובץ: {e}")
        return

    # מילון לשמירת תוכן ההערות ומחבריהן
    comments_dict = {}
    if comments_xml:
        comments_tree = ET.fromstring(comments_xml)
        for comment in comments_tree.findall('.//w:comment', ns):
            c_id = comment.attrib.get(f"{{{ns['w']}}}id")
            c_text = "".join([t.text for t in comment.findall('.//w:t', ns) if t.text])
            author = comment.attrib.get(f"{{{ns['w']}}}author", "לא ידוע")
            comments_dict[c_id] = (author, c_text)

    # עיבוד המסמך המרכזי לקבלת הטקסט המלא עם מעקב השינויים
    doc_tree = ET.fromstring(document_xml)
    full_text = parse_element(doc_tree, ns)

    # כתיבת התוצרים לקובץ חדש
    output_path = docx_path.replace('.docx', '_full_context.txt')
    try:
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write("=== טקסט המסמך (כולל מעקב שינויים וסימוני הערות) ===\n\n")
            f.write(full_text)
            f.write("\n\n=== פירוט ההערות (מילון מונחים) ===\n\n")
            for c_id, (author, text) in comments_dict.items():
                f.write(f"הערה {c_id} (מאת {author}): {text}\n")
                f.write("-" * 40 + "\n")
        print(f"החילוץ בוצע בהצלחה! התוצרים המלאים נשמרו בקובץ:\n{output_path}")
    except Exception as e:
        print(f"שגיאה בשמירת קובץ התוצאות: {e}")

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("שימוש: python3 extract_full_context.py <file.docx>")
    else:
        file_path = sys.argv[1]
        if not os.path.exists(file_path):
            print("הקובץ לא נמצא. אנא ודא שהנתיב תקין.")
        else:
            extract_full_context(file_path)