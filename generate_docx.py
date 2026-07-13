from docx import Document
from docx.shared import Pt, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH

doc = Document()

# Title
title = doc.add_heading('Sentinel AI \u2014 Low-Budget Deployment Strategy', 0)
title.alignment = WD_ALIGN_PARAGRAPH.CENTER

# Metadata
p = doc.add_paragraph()
p.add_run('Prepared for: ').bold = True
p.add_run('Project Manager, Adiva Technology\n')
p.add_run('Objective: ').bold = True
p.add_run('Deploy Sentinel AI across a 10,000-camera school campus within a highly constrained budget (Rs. 3 \u2013 10 Lakhs), utilizing either existing low-end (CPU-only) or high-end (GPU) computers.')

# Section 1
doc.add_heading('1. The 10,000 Camera Challenge', level=1)
doc.add_paragraph('Processing 10,000 high-definition video streams in real-time requires massive continuous compute power (approx. 500 dedicated GPUs, costing upwards of Rs. 50 Crore over 5 years).')
doc.add_paragraph('To fit a Rs. 3\u201310 Lakh budget, the system must shift from continuous 24/7 processing to smart, targeted processing. We achieve this by blending forensic (post-incident) search, motion-triggered alerts, and strategic live-monitoring.')

# Section 2
doc.add_heading('2. Deployment Options (Based on Available Hardware)', level=1)

# Option A
doc.add_heading('Option A: The "Forensic & Night-Watch" Model', level=2)
p = doc.add_paragraph()
p.add_run('Best for: ').bold = True
p.add_run('Schools with standard, low-end office PCs (No GPUs).\n')
p.add_run('Strategy: ').bold = True
p.add_run('Zero hardware upgrades. The system does not watch 10,000 cameras live during the day.')
doc.add_paragraph('Daytime (Forensic Investigator): The software sits idle until an incident is reported. Staff select the time and location, and the CPU processes the saved footage faster than real-time to locate the incident.', style='List Bullet')
doc.add_paragraph('Nighttime (Motion-Triggered): Sentinel links to the existing camera\'s basic motion sensors. If motion is detected in a dark hallway, the PC wakes up, grabs the 10-second clip, verifies human presence, and fires an alert.', style='List Bullet')
p = doc.add_paragraph(style='List Bullet')
p.add_run('Costing Estimate:\n').bold = True
p.add_run('   - Hardware Cost: Rs. 0 (Reuses existing PCs).\n')
p.add_run('   - Adiva Software/Installation Fee: Rs. 1.5L \u2013 2.5L / year.')
doc.add_paragraph('Capacity: Can easily service 10,000 cameras because it only processes video on demand.', style='List Bullet')

# Option B
doc.add_heading('Option B: The Edge AI USB Accelerator Upgrade', level=2)
p = doc.add_paragraph()
p.add_run('Best for: ').bold = True
p.add_run('Upgrading weak PCs to handle live AI streams without buying expensive GPU servers.\n')
p.add_run('Strategy: ').bold = True
p.add_run('We plug USB-based AI coprocessors (like the Google Coral USB Accelerator or Intel Neural Compute Stick) into the school\'s existing weak computers.')
doc.add_paragraph('How it Works: These Rs. 6,000 USB sticks contain dedicated Neural Processing Units (NPUs). When plugged in via USB 3.0, they take over 100% of the heavy YOLO object-detection math, completely freeing up the weak CPU.', style='List Bullet')
doc.add_paragraph('Scale: One weak PC with two Coral USBs plugged in can instantly process 10\u201320 cameras live, or hundreds of cameras if paired with motion-triggering. A cluster of 10 weak PCs equipped with 20 Coral sticks can provide a massive AI grid.', style='List Bullet')
p = doc.add_paragraph(style='List Bullet')
p.add_run('Costing Estimate:\n').bold = True
p.add_run('   - Hardware (20x USB Accelerators): ~Rs. 1.2L \u2013 1.5L.\n')
p.add_run('   - Existing PCs: Rs. 0.\n')
p.add_run('   - Adiva Software/Margin Fee: Rs. 2.5L \u2013 4L / year.')
doc.add_paragraph('Capacity: Secures 500\u20131,000 high-risk cameras live, leaving the remaining 9,000 for forensic search.', style='List Bullet')

# Option C
doc.add_heading('Option C: The Targeted "Live Shield"', level=2)
p = doc.add_paragraph()
p.add_run('Best for: ').bold = True
p.add_run('Schools that already own 1-3 high-end GPU computers (or are willing to buy them out of the Rs. 10L budget).\n')
p.add_run('Strategy: ').bold = True
p.add_run('We dedicate the GPU power exclusively to the most dangerous or critical areas of the campus.')
doc.add_paragraph('How it Works: The school selects their Top 150 High-Risk Cameras (main gates, perimeter fences, server rooms). The GPU PCs monitor these 150 cameras live, 24/7, providing instant alerts for violence, intrusion, or loitering. The remaining 9,850 cameras run in Forensic Mode.', style='List Bullet')
p = doc.add_paragraph(style='List Bullet')
p.add_run('Costing Estimate:\n').bold = True
p.add_run('   - Hardware (if buying 2-3 new GPU PCs): ~Rs. 2.5L \u2013 3.5L.\n')
p.add_run('   - Adiva Software/Installation Fee: Rs. 3L \u2013 5L / year.')
doc.add_paragraph('Capacity: 100\u2013200 cameras live; 10,000 cameras forensic.', style='List Bullet')

# Section 3
doc.add_heading('3. Financial Summary & Margin Potential for Adiva', level=1)
doc.add_paragraph('By shifting the architecture away from heavy servers, Adiva can secure the contract within the school\'s budget while maintaining excellent profit margins.')

table = doc.add_table(rows=1, cols=5)
table.style = 'Table Grid'
hdr_cells = table.rows[0].cells
hdr_cells[0].text = 'Deployment Model'
hdr_cells[1].text = 'School CapEx (Hardware)'
hdr_cells[2].text = 'Adiva Software/AMC'
hdr_cells[3].text = 'Total 1st Year Cost'
hdr_cells[4].text = 'Adiva Margin Focus'

row_cells = table.add_row().cells
row_cells[0].text = 'A. Forensic (Low-end PCs)'
row_cells[1].text = 'Rs. 0'
row_cells[2].text = 'Rs. 1.5L \u2013 2.5L'
row_cells[3].text = 'Rs. 1.5L \u2013 2.5L'
row_cells[4].text = '100% Software Margin'

row_cells = table.add_row().cells
row_cells[0].text = 'B. USB AI Accelerators'
row_cells[1].text = 'Rs. 1.5L'
row_cells[2].text = 'Rs. 2.5L \u2013 4.0L'
row_cells[3].text = 'Rs. 4.0L \u2013 5.5L'
row_cells[4].text = 'Hardware Markup + Software'

row_cells = table.add_row().cells
row_cells[0].text = 'C. Targeted Live (GPU PCs)'
row_cells[1].text = 'Rs. 3.5L'
row_cells[2].text = 'Rs. 3.0L \u2013 5.0L'
row_cells[3].text = 'Rs. 6.5L \u2013 8.5L'
row_cells[4].text = 'High-Value SLA & Software'

# Section 4
doc.add_heading('4. Recommendation for the Client Pitch', level=1)
doc.add_paragraph('Adiva should present Option B (The USB Accelerator Upgrade) as the primary recommendation.')

p = doc.add_paragraph()
p.add_run('The Pitch Script:\n').bold = True
p.add_run('"Upgrading 10,000 cameras to AI usually costs Crores in server infrastructure. We have a smarter way. We will take your existing, low-end computers and upgrade them using Google Coral AI Accelerators via USB. For just Rs. 4 to 5 Lakhs, we will transform your current IT room into a decentralized AI brain. This will give you live, real-time alerts on your 500 most critical cameras, and instant Forensic Search capabilities across the other 9,500. No massive servers required."').italic = True

doc.save(r'C:\Users\haron\Desktop\SENTINEL 2.0\Adiva_Budget_Deployment_Proposal.docx')
