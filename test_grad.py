import sys, io
sys.path.insert(0, "/home/robertpiyyra/id_project")
from reportlab.pdfgen.canvas import Canvas
from reportlab.lib.colors import Color

c = Canvas("/tmp/test_grad.pdf")
c.setFont("Helvetica", 40)

c.saveState()
t = c.beginText(100, 400)
t.setFont("Helvetica", 40)
t.setRenderMode(7)
t.textOut("Gradient Test")
c.drawText(t)

c.linearGradient(100, 440, 100, 400, (Color(1,0,0), Color(0,0,1)))
c.restoreState()

c.save()
print("Success")
