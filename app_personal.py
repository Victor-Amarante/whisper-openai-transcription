import os
from datetime import datetime
import time
import queue

from streamlit_webrtc import WebRtcMode, webrtc_streamer
import streamlit as st

import pydub
from pydub import AudioSegment
import openai
from dotenv import load_dotenv, find_dotenv


# --- ORGANIZAÇÃO DO DIRETÓRIO ---
PASTA_ARQUIVOS = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'arquivos')
os.makedirs(PASTA_ARQUIVOS, exist_ok=True)

# --- PROMPT PARA O COMANDO ---
PROMPT = '''
Faça o resumo do texto delimitado por #### 
O texto é a transcrição de uma reunião.
O resumo deve contar com os principais assuntos abordados.
O resumo não deve ter limite de caracteres.
O resumo deve estar em texto corrido.
No final, devem ser apresentados todos os pontos mais importantes na reunião no formato de bullet points.
O formato final que eu desejo é:

Resumo reunião:
- escrever aqui o resumo.

Tópicos importantes da reunião:
- tópico 1
- tópico 2
- tópico 3
- tópico 4
- tópico 5
- tópico n

texto: ####{}####
'''

# funcao para salvar o arquivo
def salva_arquivo(caminho_arquivo, conteudo):
    with open(caminho_arquivo, 'w') as f:
        f.write(conteudo)

# funcao para ler arquivo
def le_arquivo(caminho_arquivo, encoding='utf-8'):
    if os.path.exists(caminho_arquivo):
        with open(caminho_arquivo, encoding=encoding, errors='replace') as f:
            return f.read()
    else:
        return ''

# funcao para listar as reunioes
def listar_reunioes():
    lista_reunioes = [os.path.join(PASTA_ARQUIVOS, item) for item in os.listdir(PASTA_ARQUIVOS)]
    lista_reunioes.sort(reverse=True)
    reunioes_dict = {}
    for pasta_reuniao in lista_reunioes:
        data_reuniao = os.path.basename(pasta_reuniao)
        if len(data_reuniao.split('_')) == 6:  # Verifica se a string está no formato esperado
            ano, mes, dia, hora, minuto, segundo = data_reuniao.split('_')
            reunioes_dict[data_reuniao] = f'{ano}/{mes}/{dia} {hora}:{minuto}:{segundo}'
            titulo = le_arquivo(os.path.join(pasta_reuniao, 'titulo.txt'))
            if titulo != '':
                reunioes_dict[data_reuniao] += f' - {titulo}'
    return reunioes_dict



# --- OPENAI UTILS ---
load_dotenv()
client = openai.OpenAI(api_key=os.getenv('API_KEY'))

def transcreve_audio(caminho_audio, language='pt', response_format='text'):
    with open(caminho_audio, 'rb') as arquivo_audio:
        transcricao = client.audio.transcriptions.create(
            model='whisper-1',
            language=language,
            response_format=response_format,
            file=arquivo_audio,
        )
    return transcricao

def chat_openai(mensagem, modelo='gpt-3.5-turbo-1106'):
    mensagens = [{'role': 'user', 'content': mensagem}]
    resposta = client.chat.completions.create(
        model=modelo,
        messages=mensagens,
        )
    return resposta.choices[0].message.content


# --- OPÇÃO 1: GRAVA REUNIÃO ---
def adiciona_chunck_audio(frames_de_audio, audio_chunck):
    for frame in frames_de_audio:
        sound = pydub.AudioSegment(
            data=frame.to_ndarray().tobytes(),
            sample_width=frame.format.bytes,
            frame_rate=frame.sample_rate,
            channels=len(frame.layout.channels),
        )
        audio_chunck += sound
    return audio_chunck

def processa_audio(pasta_reuniao):
    audio_path = os.path.join(pasta_reuniao, 'audio.mp3')
    audio = AudioSegment.from_file(audio_path)

    # definir a duracao maxima de cada chunk em ms para 10 min
    chunk_duration = 10 * 60 * 1000

    # dividir o audio em chunks de 10 min
    chunks = []
    start = 0
    while start < len(audio):
        end = min(start + chunk_duration, len(audio))
        chunk = audio[start:end]
        chunks.append(chunk)
        start = end

    # enviar cada chunk para a api do whisper-openai para transcricao
    transcricoes = []
    for i, chunk in enumerate(chunks):
        chunk_path = os.path.join(pasta_reuniao, f'audio_chunk_{i}.mp3')
        chunk.export(chunk_path, format='mp3')
        with open(chunk_path, 'rb') as chunk_file:
            transcription = client.audio.transcriptions.create(
                model='whisper-1',
                file=chunk_file
            )
            transcricoes.append(transcription.text)
        os.remove(chunk_path)
    
    transcricao_completa = ' '.join(transcricoes)
    salva_arquivo(os.path.join(pasta_reuniao, 'transcricao.txt'), transcricao_completa)
    return transcricao_completa

def tab_grava_reuniao():
    webrtx_ctx = webrtc_streamer(
        key='recebe_audio',
        mode=WebRtcMode.SENDONLY,
        audio_receiver_size=1024,
        media_stream_constraints={'video': False, 'audio': True},
    )

    if not webrtx_ctx.state.playing:
        return

    container = st.empty()
    container.markdown('## Pode iniciar o bate-papo...\n##### Assim que finalizar, salvar e analisar o resumo feito.\n##### Boa reunião!')
    st.success('Gravação em andamento...')
    pasta_reuniao = os.path.join(PASTA_ARQUIVOS, datetime.now().strftime('%Y_%m_%d_%H_%M_%S'))
    os.makedirs(pasta_reuniao)

    ultima_transcricao = time.time()
    audio_completo = pydub.AudioSegment.empty()
    audio_chunck = pydub.AudioSegment.empty()
    transcricao = ''

    while True:
        if webrtx_ctx.audio_receiver:
            try:
                frames_de_audio = webrtx_ctx.audio_receiver.get_frames(timeout=1)
            except queue.Empty:
                time.sleep(0.1)
                continue
            audio_completo = adiciona_chunck_audio(frames_de_audio, audio_completo)
            audio_chunck = adiciona_chunck_audio(frames_de_audio, audio_chunck)
            if len(audio_chunck) > 0:
                audio_completo.export(os.path.join(pasta_reuniao, 'audio.mp3'))
                agora = time.time()
                if agora - ultima_transcricao > 5:
                    ultima_transcricao = agora
                    audio_chunck.export(os.path.join(pasta_reuniao, 'audio_temp.mp3'))
                    if len(audio_chunck) >= 100:  # Verificar se o áudio tem pelo menos 0.1 segundos
                        transcricao_chunck = transcreve_audio(os.path.join(pasta_reuniao, 'audio_temp.mp3'))
                        transcricao += transcricao_chunck
                        salva_arquivo(os.path.join(pasta_reuniao, 'transcricao.txt'), transcricao)
                        container.markdown(transcricao)
                    audio_chunck = pydub.AudioSegment.empty()
        else:
            break

    # Processar o áudio após o término da gravação
    transcricao = processa_audio(pasta_reuniao)
    container.markdown(transcricao)


# --- OPÇÃO 2: SELEÇÃO REUNIÃO ---
def tab_selecao_reuniao():
    reunioes_dict = listar_reunioes()
    if len(reunioes_dict) > 0:
        reuniao_selecionada = st.selectbox('Selecione uma reunião',
                                        list(reunioes_dict.values()))
        st.divider()
        reuniao_data = [k for k, v in reunioes_dict.items() if v == reuniao_selecionada][0]
        pasta_reuniao = os.path.join(PASTA_ARQUIVOS, reuniao_data)
        if not os.path.exists(os.path.join(pasta_reuniao, 'titulo.txt')):
            st.warning('Adicione um titulo')
            titulo_reuniao = st.text_input('Título da reunião')
            st.button('Salvar',
                      on_click=salvar_titulo,
                      args=(pasta_reuniao, titulo_reuniao))
        else:
            titulo = le_arquivo(os.path.join(pasta_reuniao, 'titulo.txt'), encoding='utf-8')

            transcricao = le_arquivo(os.path.join(pasta_reuniao, 'transcricao.txt'))
            resumo = le_arquivo(os.path.join(pasta_reuniao, 'resumo.txt'))
            if resumo == '':
                gerar_resumo(pasta_reuniao)
                resumo = le_arquivo(os.path.join(pasta_reuniao, 'resumo.txt'))
            st.markdown(f'## {titulo}')
            st.markdown(f'{resumo}')
            st.markdown(f'Transcricao: {transcricao}')
        
def salvar_titulo(pasta_reuniao, titulo):
    salva_arquivo(os.path.join(pasta_reuniao, 'titulo.txt'), titulo)

def gerar_resumo(pasta_reuniao):
    transcricao = le_arquivo(os.path.join(pasta_reuniao, 'transcricao.txt'))
    resumo = chat_openai(mensagem=PROMPT.format(transcricao))
    salva_arquivo(os.path.join(pasta_reuniao, 'resumo.txt'), resumo)


# --- CORE DO SISTEMA WEB --- 
st.set_page_config(page_title='YATT - Yet Another Transcription Tool', page_icon='🌐', layout='wide')
def main():
    st.header('Bem-vindo ao YATT - Yet Another Transcription Tool 🎙️', divider=True)
    with st.sidebar:
        st.image('https://www.onepointltd.com/wp-content/uploads/2024/02/ONE-POINT-01-1.png')
        st.title('Sistema de IA para Reuniões')
        choices = st.radio('O que deseja fazer:', ('Gravar reunião', 'Selecionar reunião'))
        st.info('Este projeto tem como objetivo aplicar os conhecimentos de IA (Inteligência Artificial) para criar uma ferramenta de transcrição de áudio para texto através da análise e processamento de áudio em tempo real')
    if choices == 'Gravar reunião':
        tab_grava_reuniao()
    if choices == 'Selecionar reunião':
        tab_selecao_reuniao()

if __name__ == '__main__':
    main()
