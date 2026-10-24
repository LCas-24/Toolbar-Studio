# Toolbar-Studio
(Vibe-Coded)
A lightweight Firefox theme that adds custom GIF imagery and spacing to the tab bar, offering a playful and minimal visual upgrade.

Uma ferramenta que corta imagens e posiciona GIFs no fundo da barra do Firefox, com biblioteca de fotos e de GIFs.

> Funciona no **Windows** e no **Linux**. Tudo roda no seu computador e nada é enviado para a internet.

## O que você precisa

- Windows ou Linux
- Firefox (versão normal, Snap ou Flatpak)
- Python 3. No Windows, [baixe aqui](https://www.python.org/downloads/) e marque **Add Python to PATH** na instalação. No Linux ele já vem instalado na maioria das distribuições (confira com `python3 --version`).
- Um segundo navegador (Edge no Windows; Chrome, Chromium, Brave ou outro no Linux) para abrir o painel, porque o Firefox é fechado e reaberto a cada alteração

## Passo a passo (LEIA COM ATENÇÃO!)

### Passo 1: liberar temas personalizados no Firefox

1. Digite `about:config` na barra de endereços do Firefox e aperte Enter.
2. Clique em **Aceitar o risco e continuar**.
3. Na caixa de pesquisa, procure por `toolkit.legacyUserProfileCustomizations.stylesheets`.
4. Clique duas vezes na opção para mudar o valor de `false` para `true`.

Sem isso o Firefox ignora o tema.

### Passo 2: baixar a ferramenta

1. Nesta página, clique em **Code** e depois em **Download ZIP**.
2. Extraia o ZIP numa pasta qualquer, por exemplo em Documentos.

**Não coloque estes arquivos dentro da pasta do perfil do Firefox.** A ferramenta cria a pasta `chrome` e o arquivo `userChrome.css` sozinha, no lugar certo.

### Passo 3: iniciar

1. Abra um terminal dentro da pasta onde você extraiu os arquivos.
   - **Windows:** clique na barra de endereços do Explorador de Arquivos, digite `cmd` e aperte Enter.
   - **Linux:** clique com o botão direito na pasta e escolha "Abrir no terminal", ou use `cd` até ela.
2. Digite:

```
python servidor.py        # Windows (se der erro, tente: py servidor.py)
python3 servidor.py       # Linux
```

3. A ferramenta acha o perfil do Firefox sozinha e abre o painel em outro navegador (Edge no Windows; no Linux, o primeiro que ela encontrar entre Edge, Chrome, Chromium, Brave e outros). Se a página não abrir, copie o endereço que aparece no terminal e cole no navegador.
4. Se o terminal pedir o caminho do perfil, digite `about:support` no Firefox, procure por **Pasta do Perfil** (ou **Profile Folder**), copie o caminho e cole no terminal.

**Deixe o terminal aberto enquanto estiver usando.** Para parar, aperte `Ctrl+C`.

> **Só tem o Firefox no Linux?** O painel abre nele e fecha quando o Firefox reinicia ao aplicar. Basta abrir de novo o mesmo endereço do terminal, ou instalar outro navegador.

> Abra sempre o painel pelo endereço que o terminal mostra. Ele muda a cada vez que você inicia a ferramenta.

### Passo 4: usar

**Imagem (PNG)**
1. Clique em **Escolher imagem** ou arraste uma imagem para a área de edição.
2. Arraste para enquadrar, use o scroll ou o zoom para aproximar e **Girar 90°** se precisar.
3. Clique em **Aplicar no Firefox**.

**GIF**
1. Clique em **Escolher GIF** ou arraste um GIF para a área de edição.
2. Arraste o GIF até a posição desejada e ajuste a altura no controle deslizante.
3. Clique em **Aplicar no Firefox**.

**Bibliotecas**
- As imagens e os GIFs que você aplica entram na biblioteca automaticamente.
- Clique num item da biblioteca para abrir no editor.
- Use a ★ para favoritar (favoritos aparecem primeiro) e o × para excluir.

> **Atenção:** ao aplicar, o Firefox fecha e abre de novo (suas abas são restauradas). Salve o que estiver fazendo antes.

## Ajustes

- **Largura da tela** e **Altura das barras** (no topo do painel) devem bater com o tamanho real da sua barra no Firefox, em pixels do CSS. A largura já vem preenchida; ajuste a altura se o GIF ficar diferente do que aparece na prévia.
- Para o GIF não mudar de posição quando a barra de favoritos some, adicione isto no final do `userChrome.css` (na pasta `chrome` do perfil), trocando `1078px` pelo valor de `right` que está no início do arquivo e ajustando o `50px`:

```css
#navigator-toolbox:has(#PersonalToolbar[collapsed="true"]) {
    background-position: right calc(1078px + 50px) top 0px, 80% center !important;
}
```

## Remover

- Use os botões **Remover fundo atual** (imagem e GIF) no painel, ou
- apague a pasta `chrome` do perfil do Firefox. O arquivo `userChrome.css.bak` guarda a versão anterior do tema.

## Problemas comuns

| Problema | O que fazer |
|---|---|
| Nada muda no Firefox | Confira o Passo 1 e reinicie o Firefox. |
| "Pedido recusado" no painel | Reabra a página pelo endereço que o terminal mostrou. |
| Imagem borrada | Use uma imagem mais larga que a tela, o painel avisa. |
| `python` não é reconhecido | Windows: reinstale o Python marcando **Add Python to PATH** ou use `py servidor.py`. Linux: use `python3`. |
| Linux: a ferramenta pegou o perfil errado | Defina o caminho: `FIREFOX_PROFILE="/caminho/do/perfil" python3 servidor.py` (veja o caminho em `about:support`). |
| Linux: o Firefox não reabre sozinho | Abra-o manualmente. A ferramenta procura `firefox`, `/snap/bin/firefox` e o Flatpak `org.mozilla.firefox`. |

## Privacidade e segurança

- O servidor só aceita conexões do próprio computador.
- Cada vez que inicia, ele gera uma senha temporária que só o painel conhece.
- As pastas `biblioteca` e `biblioteca_gif` ficam ao lado de `servidor.py` e guardam as suas imagens. Não compartilhe essas pastas.
