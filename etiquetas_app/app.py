from flask import Flask, render_template, request, redirect, flash, url_for, send_file, session, json
from flask_sqlalchemy import SQLAlchemy
import os
import sys
from datetime import datetime
from reportlab.pdfgen import canvas
from reportlab.lib.units import mm
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.lib.utils import ImageReader
import barcode
from barcode.writer import ImageWriter
import qrcode
import time
from reportlab.platypus import Table, TableStyle
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication
import webbrowser
import threading

app = Flask(__name__)
app.secret_key = 'clave-secreta'

# Configuración de base de datos
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///usuarios.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

# --- FUNCIONES DE RUTA COMPATIBLES CON PyInstaller ---
def resource_path(relative_path):
    """Devuelve la ruta absoluta al recurso, compatible con PyInstaller."""
    if hasattr(sys, '_MEIPASS'):
        base_path = sys._MEIPASS
    else:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)

# MODELOS
class Usuario(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    correo = db.Column(db.String(100), unique=True, nullable=False)
    contraseña = db.Column(db.String(100), nullable=False)

class Producto(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(100), nullable=False)
    codigo = db.Column(db.String(50), unique=True, nullable=False)
    precio = db.Column(db.Float, nullable=False)
    imagen = db.Column(db.String(200), nullable=True)
    descripcion = db.Column(db.Text, nullable=True)
    categoria = db.Column(db.String(50), nullable=True)

class Pedido(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    fecha = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    nombre_cliente = db.Column(db.String(100), nullable=False)
    direccion = db.Column(db.String(200), nullable=False)
    ciudad = db.Column(db.String(100), nullable=False)
    codigo_postal = db.Column(db.String(20), nullable=False)
    telefono = db.Column(db.String(20), nullable=False)
    productos = db.Column(db.Text, nullable=False)
    total = db.Column(db.Float, nullable=False)

# RUTAS
@app.route('/', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        correo = request.form['correo']
        contraseña = request.form['contraseña']
        usuario = Usuario.query.filter_by(correo=correo, contraseña=contraseña).first()
        if usuario:
            session['usuario_id'] = usuario.id
            session['correo'] = usuario.correo
            return redirect('/productos')
        else:
            flash('Credenciales incorrectas')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect('/')

@app.route('/productos')
def mostrar_productos():
    if 'usuario_id' not in session:
        return redirect('/')
    productos = Producto.query.all()
    return render_template('productos.html', productos=productos)

@app.route('/categoria/<categoria>')
def productos_categoria(categoria):
    productos = Producto.query.filter_by(categoria=categoria).all()
    return render_template('productos_categoria.html', productos=productos, categoria=categoria)

@app.route('/producto/<int:producto_id>')
def detalle_producto(producto_id):
    producto = Producto.query.get_or_404(producto_id)
    return render_template('detalle.html', producto=producto)

@app.route('/reservar/<int:producto_id>', methods=['POST'])
def reservar_producto(producto_id):
    producto = Producto.query.get_or_404(producto_id)
    if 'carrito' not in session:
        session['carrito'] = []
    carrito = session['carrito']
    item_existente = next((item for item in carrito if item['id'] == producto.id), None)
    if item_existente:
        item_existente['cantidad'] += 1
    else:
        carrito.append({
            'id': producto.id,
            'nombre': producto.nombre,
            'precio': float(producto.precio),
            'cantidad': 1,
            'imagen': producto.imagen
        })
    session['carrito'] = carrito
    flash(f'{producto.nombre} añadido al carrito')
    return redirect(url_for('ver_carrito'))

@app.route('/carrito')
def ver_carrito():
    carrito = session.get('carrito', [])
    total = sum(item['precio'] * item['cantidad'] for item in carrito)
    return render_template('carrito.html', carrito=carrito, total=total)

@app.route('/eliminar_del_carrito/<int:producto_id>', methods=['POST'])
def eliminar_del_carrito(producto_id):
    carrito = session.get('carrito', [])
    nuevo_carrito = [item for item in carrito if item['id'] != producto_id]
    session['carrito'] = nuevo_carrito
    flash('Producto eliminado del carrito')
    return redirect(url_for('ver_carrito'))

def crear_y_guardar_pedido(form, carrito):
    nuevo_pedido = Pedido(
        nombre_cliente=form['nombre'],
        direccion=form['direccion'],
        telefono=form['telefono'],
        ciudad=form['ciudad'],
        codigo_postal=form['codigo_postal'],
        productos=json.dumps(carrito),
        total=sum(item['precio'] * item['cantidad'] for item in carrito)
    )
    db.session.add(nuevo_pedido)
    db.session.commit()
    return nuevo_pedido

def generar_factura_para_pedido(pedido, carrito):
    factura_path = generar_factura_pdf(pedido, carrito)
    print(f"Factura generada en: {factura_path}, existe: {os.path.exists(factura_path)}")
    return factura_path

def generar_etiqueta_para_pedido(form, id_envio):
    return generar_etiqueta_envio_pdf(
        form['nombre'],
        form['direccion'],
        form['telefono'],
        form['ciudad'],
        form['codigo_postal'],
        id_envio
    )

def enviar_factura_email(form, factura_path):
    enviar_factura_por_email(
        destinatario=form['correo'],
        asunto="Factura de su pedido",
        cuerpo="Adjuntamos la factura de su compra. ¡Gracias por confiar en nosotros!",
        pdf_path=factura_path,
        remitente="facturacion@infraazure.local",
        clave="FCT@2024",
        servidor="172.20.10.11",
        puerto=25
    )

@app.route('/finalizar_compra', methods=['GET', 'POST'])
def finalizar_compra():
    carrito = session.get('carrito', [])
    if not carrito:
        flash('Carrito vacío')
        return redirect(url_for('ver_carrito'))
    if request.method == 'POST':
        nuevo_pedido = crear_y_guardar_pedido(request.form, carrito)
        session['carrito'] = []
        factura_path = generar_factura_para_pedido(nuevo_pedido, carrito)
        id_envio = f"{datetime.utcnow().strftime('%Y%m%d')}{request.form['telefono'][-4:]}"
        generar_etiqueta_para_pedido(request.form, id_envio)
        enviar_factura_email(request.form, factura_path)
        return redirect(url_for('gracias'))
    return render_template('finalizar_compra.html')

@app.route('/gracias')
def gracias():
    return render_template('gracias.html')

@app.route('/barcode/<id_envio>')
def generar_barcode(id_envio):
    carpeta = resource_path(os.path.join("static", "barcode"))
    os.makedirs(carpeta, exist_ok=True)
    ruta = os.path.join(carpeta, f"{id_envio}.png")
    if not os.path.exists(ruta):
        code128 = barcode.get('code128', id_envio, writer=ImageWriter())
        code128.save(ruta, options={'write_text': False})
    return send_file(ruta, mimetype='image/png')

@app.route('/qr/<pedido_id>')
def generar_qr(pedido_id):
    import io
    pedido = Pedido.query.get_or_404(pedido_id)
    productos = json.loads(pedido.productos)
    detalle = "\n".join([f"{p['nombre']} x{p['cantidad']}" for p in productos])
    qr_data = f"""BEGIN:VCARD
VERSION:3.0
FN:{pedido.nombre_cliente}
ADR:;;{pedido.direccion};{pedido.ciudad};;{pedido.codigo_postal};España
TEL:{pedido.telefono}
NOTE:ID: {pedido_id}
PRODUCTOS:
{detalle}
END:VCARD"""
    qr = qrcode.QRCode(version=1, box_size=10, border=4)
    qr.add_data(qr_data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    img_io = io.BytesIO()
    img.save(img_io, 'PNG')
    img_io.seek(0)
    return send_file(img_io, mimetype='image/png')

def crear_db():
    with app.app_context():
        db.create_all()
        if not Usuario.query.first():
            usuarios = [
                ('fvm@hispa.es', 'FVM@2025'),
                ('fts@bengala.fr', 'FTS@2025'),
                ('jmg@medusa.com', 'JMG@2025'),
                ('apr@infraazure.local', 'APR@2025')
            ]
            for correo, contraseña in usuarios:
                db.session.add(Usuario(
                    correo=correo,
                    contraseña=contraseña
                ))
            productos = [
                ('CACHIMBA STEAMULATION PRO X II', '210196-5', 399.00, 'img/cachimba-steamulation-classic-pro-x-ii.jpg', 'Llega a Bengala Spain la nueva, mejorada e innovadora CACHIMBA STEAMULATION PRO X II con un nuevo diseño más moderno y estilizado. Esta cachimba está pensada por y para auténticos experimentados de la shisha, ya que la marca a patentado unos nuevos sistemas los cuales puedes variar la restricción de tu fumada y adaptar a tu gusto la experiencia de la hookah.', 'Cachimbas'),
                ('CACHIMBA DMNT KORVUS', '220316-1', 159.95, 'img/dmt.jpg', 'La tan exclusiva y rebelde marca DMNT vuelve a superarse, esta vez trayendo una de sus mejores creaciones: la enigmática y misteriosa cachimba DMNT KORVUS. Una shisha con un diseño auténtico, rompedor y provocativo, capaz de llamar la atención de cualquiera.', 'Cachimbas'),
                ('CACHIMBA MEDUSE SEABLOOM', '520216-1', 432.95, 'img/cachimba-meduse-seabloom.jpg', 'La prestigiosa y exclusiva marca SEABLOOM BY MEDUSE vuelve al mercado con estos sorprendentes y llamativos modelos Seabloom. Una cachimba exclusiva, única y peculiar, no pasará desapercibida por donde pase.', 'Cachimbas'),
                ('Pod Desechable Packspod', 'VAP-001', 5.95, 'img/vaper.jpg', 'Llegan los nuevos PACKSPOD, un tamaño reducido para aumentar la comodidad sin reducir su rendimiento, para que disfrutes de tus sabores preferidos. Los pods desechables Packspod logran un equilibrio perfecto entre sabor y vapor. Disfruta de los mejores sabores de la mano de packspod.', 'Vapers'),
                ('SALES DE NICOTINA BENGALA SALT - BRACULA 10ML', 'VAP-002', 6.95, 'img/sales-de-nicotina-bengala-salt-10ml-bracula.jpg', '¡Revive el clásico sabor del caramelo de fresa con Brácula de Bengala Salt! Este sabor captura la esencia nostálgica y dulce de las fresas más jugosas, fusionadas con el toque suave del caramelo, creando una experiencia de vapeo cautivadora y deliciosa', 'Vapers'),
                ('PACK POD RECARGABLE RINCOE MANTO NANO PRO 2', '240044-4', 34.95, 'img/pod-recargable-rincoe-manto-nano-pro-2.jpg', 'El Rincoe Manto Nano Pro 2 es un dispositivo de vapeo compacto y potente para sales de nicotina, ideal tanto para RDL (Directo a Pulmón Restringido) como para MTL (Boca a Pulmón). ', 'Packs'),
                ('Pack 3 Pods Desechables Flask', '240008-1', 14.95, 'img/pack-3-pods-flask-1100x1100.jpg', '¿Quieres empezar con los Pods Desechables Packs y no sabes que sabor elegir? ¿Quieres probar distintos sabores? El Pack Pods Desechables Flask es perfecto para ti. Prueba sabores y marcas que no habias probado antes. Llévate 3 Pods Desechables a un precio increible.', 'Packs'),
                ('Manguera de Silicona Leopardo Azul', '245000-1', 7.60, 'img/leopardo-azul.jpg', 'La manguera de silicona Leopardo Azul es tipo "soft-touch", es decir, que ofrece un tacto suave y agradable.', 'Mangueras'),
                ('Boquilla Slim XL', '280000-1', 7.96, 'img/slim-xl.jpg', 'Las boquilla Slim XL es un accesorio que destaca, principalmente, por tener un diseño ultra fino y un peso muy ligero, convirtiéndola en una boquilla muy fácil de manejar y utilizar.', 'Mangueras'),
                ('Cazoleta HC HighFire Sneaker Rosa', '120616-1', 29.95, 'img/cazoleta-hc-sneaker-pink-80x80.jpg', 'La Cazoleta HC Sneaker es una obra maestra de diseño y funcionalidad, creada para aquellos que valoran tanto la estética como el rendimiento. Inspirada en la emblemática zapatilla de deporte Jordan, esta cazoleta destaca por su forma innovadora y su inigualable calidad..', 'Cazoletas'),
                ('Cazoleta HC HighFire Sneaker Roja', '100616-1', 29.95, 'img/Cazoleta-hc-higfire-sneaker-red-900x900.jpg', 'La Cazoleta HC Sneaker es una obra maestra de diseño y funcionalidad, creada para aquellos que valoran tanto la estética como el rendimiento. Inspirada en la emblemática zapatilla de deporte Jordan, esta cazoleta destaca por su forma innovadora y su inigualable calidad..', 'Cazoletas'),
                ('Cazoleta HC HighFire Sneaker Azul', '100614-1', 29.95, 'img/Cazoleta-hc-highfire-sneaker-900x900.jpg', 'La Cazoleta HC Sneaker es una obra maestra de diseño y funcionalidad, creada para aquellos que valoran tanto la estética como el rendimiento. Inspirada en la emblemática zapatilla de deporte Jordan, esta cazoleta destaca por su forma innovadora y su inigualable calidad..', 'Cazoletas'),
                ('Cazoleta HC HighFire Sneaker Negra', '100614-9', 29.95, 'img/cazoleta-hc-sneaker-black-900x900.jpg', 'La Cazoleta HC Sneaker es una obra maestra de diseño y funcionalidad, creada para aquellos que valoran tanto la estética como el rendimiento. Inspirada en la emblemática zapatilla de deporte Jordan, esta cazoleta destaca por su forma innovadora y su inigualable calidad..', 'Cazoletas'),
                ('Carbón King Coco 26 mm', '400414-9', 7.20, 'img/king-coco-1kg.jpg', 'El carbón King Coco es uno de los más vendidos a nivel nacional y se caracteriza por estar fabricado con cáscara de coco natural. Además, este carbón no contiene pólvora en su interior, lo que hace que tengan un alto poder calorífico y una larga duración.', 'Carbones'),
                ('Carbón CocoRosé 27mm – 1 Kg', '500414-9', 6.95, 'img/Carbon-natural-cocorose-27mm-768x768.jpg', ' El Carbón Natural Premium que viene a Revolucionar el mundo de la cachimba.', 'Carbones'),
                 ('CARBÓN NATURAL ONE NATION PREMIUM 1KG 27MM', '300414-1', 6.95, 'img/carbon-natural-one-nation.jpg', 'ONE NATION Premium Shisha Cubes son carbones de shisha de alta calidad. En lugar del formato típico de 26mm x 26mm x 26mm, estos carbones se cortan a 27mm x 27mm x 27mm y, por lo tanto, son un poco más grandes que muchos otros carbones. Una vez recocidos, los carbones One Nation duran mucho tiempo y forman menos cenizas que muchos otros carbones hookah. Hecho en Indonesia Dimensiones del cubo: 27x27x27 mm 1 kg ', 'Carbones')
            ]
            for datos in productos:
                db.session.add(Producto(
                    nombre=datos[0],
                    codigo=datos[1],
                    precio=datos[2],
                    imagen=datos[3],
                    descripcion=datos[4],
                    categoria=datos[5]
                ))
            db.session.commit()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

FACTURAS_DIR = r"C:\etiquetas_app\static\facturas"
ETIQUETAS_DIR = r"C:\etiquetas_app\static\Etiquetas_por_imprimir"
BARCODE_DIR = r"C:\etiquetas_app\static\barcode"
IMG_DIR = r"C:\etiquetas_app\static\img"

def generar_factura_pdf(pedido, productos):
    print("Generando factura PDF...")
    os.makedirs(FACTURAS_DIR, exist_ok=True)
    now = datetime.now().strftime("%d_%m_%Y_%H_%M")
    pdf_path = os.path.join(FACTURAS_DIR, f"factura_{now}.pdf")
    c = canvas.Canvas(pdf_path, pagesize=letter)
    width, height = letter
    c.setFont("Helvetica-Bold", 16)
    c.drawString(50, height - 50, "InfraAzure S.L.")
    c.setFont("Helvetica", 10)
    c.drawString(50, height - 70, "Avenida de los Estudiantes, s/n, 13300 Valdepeñas, Ciudad Real, España")
    c.drawString(50, height - 85, "CIF: 536421234")
    c.drawString(50, height - 100, "Teléfono: 617 737 997")
    c.drawString(50, height - 115, "Email:adrianpina241@gmail.com")
    logo_path = os.path.join(IMG_DIR, "logo.png")
    logo_x = width - 150
    logo_y = height - 110
    logo_width = 100
    logo_height = 60
    try:
        c.drawImage(logo_path, logo_x, logo_y, width=logo_width, height=logo_height, preserveAspectRatio=True, mask='auto')
    except Exception as e:
        print(f"Error al cargar el logo: {e}")
    c.setFont("Helvetica-Bold", 12)
    c.drawString(50, height - 140, "Cliente:")
    c.setFont("Helvetica", 10)
    c.drawString(50, height - 160, pedido.nombre_cliente)
    c.drawString(50, height - 175, pedido.direccion)
    c.drawString(50, height - 190, f"{pedido.ciudad} ({pedido.codigo_postal})")
    data = [
        [f"Fecha: {pedido.fecha.strftime('%d/%m/%Y')}", '', '', ''],
        ['Producto', 'Cantidad', 'P. Unitario', 'Total']
    ]
    for p in productos:
        total = p['precio'] * p['cantidad']
        data.append([
            p['nombre'],
            str(p['cantidad']),
            f"{p['precio']:.2f} €",
            f"{total:.2f} €"
        ])
    table = Table(data, colWidths=[300, 60, 80, 80])
    table.setStyle(TableStyle([
        ('BACKGROUND', (0,1), (-1,1), colors.HexColor('#3A3A3A')),
        ('TEXTCOLOR', (0,1), (-1,1), colors.white),
        ('FONTSIZE', (0,0), (-1,1), 10),
        ('BOTTOMPADDING', (0,1), (-1,1), 12),
        ('BACKGROUND', (0,2), (-1,-1), colors.HexColor('#F5F5F5')),
        ('GRID', (0,0), (-1,-1), 1, colors.black),
        ('SPAN', (0,0), (-1,0)),
        ('ALIGN', (0,0), (-1,0), 'RIGHT'),
        ('FONTSIZE', (0,0), (-1,0), 11),
        ('BOTTOMPADDING', (0,0), (-1,0), 8),
    ]))
    table.wrapOn(c, width - 100, height)
    table.drawOn(c, 50, height - 300)
    c.setFont("Helvetica-Bold", 14)
    c.drawString(400, height - 340, f"Total: {pedido.total:.2f} €")
    c.save()
    print(f"Factura generada en: {pdf_path}, existe: {os.path.exists(pdf_path)}")
    return pdf_path

def enviar_factura_por_email(destinatario, asunto, cuerpo, pdf_path,
                           remitente="facturacion@infraazure.local",
                           clave="FCT@2025",
                           servidor="172.20.10.11",
                           puerto=25):
    msg = MIMEMultipart()
    msg['From'] = remitente
    msg['To'] = destinatario
    from email.header import Header
    msg['Subject'] = Header(asunto, 'utf-8')
    msg.attach(MIMEText(cuerpo, 'plain', 'utf-8'))
    with open(pdf_path, 'rb') as f:
        adjunto = MIMEApplication(f.read(), _subtype="pdf")
        adjunto.add_header('Content-Disposition', 'attachment',
                          filename=os.path.basename(pdf_path))
        msg.attach(adjunto)
    try:
        with smtplib.SMTP(servidor, puerto) as server:
            server.login(remitente, clave)
            server.sendmail(remitente, destinatario, msg.as_string())
        print("Correo enviado exitosamente")
    except Exception as e:
        print(f"Error enviando correo: {str(e)}")

def generar_etiqueta_envio_pdf(nombre, direccion, telefono, ciudad, codigo_postal, id_envio, referencia=None):
    os.makedirs(ETIQUETAS_DIR, exist_ok=True)
    os.makedirs(BARCODE_DIR, exist_ok=True)
    pdf_path = os.path.join(ETIQUETAS_DIR, f"{id_envio}.pdf")
    width_mm = 140
    height_mm = 80
    width = width_mm * mm
    height = height_mm * mm
    c = canvas.Canvas(pdf_path, pagesize=(width, height))
    c.setFont("Helvetica-Bold", 16)
    c.drawCentredString(width / 2, height - 15*mm, "ETIQUETA DE ENVÍO")
    c.setFont("Helvetica-Bold", 10)
    y = height - 25*mm
    x_label = 8*mm
    x_value = 40*mm
    line_height = 7*mm
    datos = [
        ("Nombre:", nombre),
        ("Dirección:", direccion),
        ("Teléfono:", telefono),
        ("Ciudad:", ciudad),
        ("C.P.:", codigo_postal),
        ("ID Envío:", id_envio)
    ]
    for label, value in datos:
        c.drawString(x_label, y, label)
        c.setFont("Helvetica", 10)
        c.drawString(x_value, y, str(value))
        c.setFont("Helvetica-Bold", 10)
        y -= line_height

    #Codigo de barras
    barcode_img_path = os.path.join(BARCODE_DIR, f"{id_envio}_barcode.png")
    code128 = barcode.get('code128', id_envio, writer=ImageWriter())
    barcode_real_path = code128.save(barcode_img_path[:-4], options={'write_text': False})
    for _ in range(20):
        if os.path.exists(barcode_real_path):
            try:
                with open(barcode_real_path, "rb") as f:
                    f.read(1)
                break
            except Exception:
                time.sleep(0.1)
        else:
            time.sleep(0.1)
    else:
        raise FileNotFoundError(f"No se pudo crear el código de barras: {barcode_real_path}")
    c.drawImage(barcode_real_path, width - 60*mm, height - 38*mm, width=45*mm, height=15*mm, preserveAspectRatio=True, mask='auto')

    # QR
    qr_img_path = os.path.join(BARCODE_DIR, f"{id_envio}_qr.png")
    vcard = f"""BEGIN:VCARD
VERSION:3.0
FN:{nombre}
TEL;TYPE=CELL:{telefono}
ADR:;;{direccion};{ciudad};;{codigo_postal};España
NOTE:ID Envío: {id_envio}
END:VCARD"""
    qr = qrcode.make(vcard)
    qr.save(qr_img_path)
    c.drawImage(qr_img_path, width - 55*mm, height - 60*mm, width=30*mm, height=30*mm, preserveAspectRatio=True, mask='auto')

    c.setStrokeColor(colors.black)
    c.setLineWidth(1)
    c.line(8*mm, 18*mm, width - 8*mm, 18*mm)
    c.setFont("Helvetica-Bold", 11)
    if referencia is None:
        referencia = id_envio
    c.drawString(10*mm, 10*mm, f"Referencia: {referencia}")
    c.save()
    print(f"Etiqueta generada en: {pdf_path}, existe: {os.path.exists(pdf_path)}")
    try:
        os.remove(barcode_real_path)
        os.remove(qr_img_path)
    except Exception:
        pass
    return pdf_path

def abrir_navegador():
    webbrowser.open_new("http://127.0.0.1:5000")

if __name__ == '__main__':
    crear_db()
    threading.Timer(1.5, abrir_navegador).start()
    app.run(host='0.0.0.0', port=5000, debug=False)

