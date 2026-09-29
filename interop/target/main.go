// Independent WebTransport target for Legilimens interoperability testing.
//
// This is NOT part of Legilimens. It is an independently-implemented WebTransport
// server built on quic-go/webtransport-go (pinned in go.mod), used to prove that
// Legilimens interoperates with a stack it does not share code with.
//
// Deterministic echo semantics (so a client can compare bytes exactly):
//   - Datagrams: each received datagram is echoed back unchanged.
//   - Bidi streams (client-opened): bytes are echoed back on the same stream,
//     preserving order; the server closes its send side when the client sends FIN.
//   - Uni streams (client-opened): the payload is read fully, then echoed back on a
//     new server-opened uni stream, tagged "ECHO:".
//   - On session establish, the server opens one uni stream tagged "INFO:" carrying
//     JSON {path, query, origin} so the client can verify path/query/origin handling.
//
// The server generates its own ECDSA P-256 certificate (SAN localhost + 127.0.0.1,
// short lifetime) and prints its base64 SHA-256(DER) hash so the caller can pin it.
//
// Stdout protocol (one per line):
//
//	CERTHASH <base64 sha256 of DER cert>
//	LISTEN <host:port>
//	READY
package main

import (
	"context"
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/sha256"
	"crypto/tls"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/base64"
	"encoding/json"
	"encoding/pem"
	"flag"
	"fmt"
	"io"
	"math/big"
	"net"
	"net/http"
	"os"
	"time"

	"github.com/quic-go/quic-go/http3"
	"github.com/quic-go/webtransport-go"
)

func genCert() (tls.Certificate, string, []byte, error) {
	priv, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		return tls.Certificate{}, "", nil, err
	}
	tmpl := &x509.Certificate{
		SerialNumber:          big.NewInt(time.Now().UnixNano()),
		Subject:               pkix.Name{CommonName: "localhost", Organization: []string{"Legilimens Interop Target"}},
		NotBefore:             time.Now().Add(-5 * time.Minute),
		NotAfter:              time.Now().Add(13 * 24 * time.Hour),
		KeyUsage:              x509.KeyUsageDigitalSignature,
		ExtKeyUsage:           []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth},
		BasicConstraintsValid: true,
		DNSNames:              []string{"localhost"},
		IPAddresses:           []net.IP{net.IPv4(127, 0, 0, 1)},
	}
	der, err := x509.CreateCertificate(rand.Reader, tmpl, tmpl, &priv.PublicKey, priv)
	if err != nil {
		return tls.Certificate{}, "", nil, err
	}
	sum := sha256.Sum256(der)
	hash := base64.StdEncoding.EncodeToString(sum[:])
	keyDER, err := x509.MarshalECPrivateKey(priv)
	if err != nil {
		return tls.Certificate{}, "", nil, err
	}
	certPEM := pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: der})
	keyPEM := pem.EncodeToMemory(&pem.Block{Type: "EC PRIVATE KEY", Bytes: keyDER})
	cert, err := tls.X509KeyPair(certPEM, keyPEM)
	return cert, hash, certPEM, err
}

func main() {
	addr := flag.String("addr", "127.0.0.1:14700", "loopback host:port to listen on")
	certOut := flag.String("certout", "", "if set, write the server certificate PEM here so a client can trust it")
	flag.Parse()
	host, _, err := net.SplitHostPort(*addr)
	if err != nil || net.ParseIP(host) == nil || !net.ParseIP(host).IsLoopback() {
		fmt.Fprintln(os.Stderr, "addr must be a numeric loopback address")
		os.Exit(1)
	}

	cert, hash, certPEM, err := genCert()
	if err != nil {
		fmt.Fprintln(os.Stderr, "cert error:", err)
		os.Exit(1)
	}
	if *certOut != "" {
		if err := os.WriteFile(*certOut, certPEM, 0o600); err != nil {
			fmt.Fprintln(os.Stderr, "certout error:", err)
			os.Exit(1)
		}
	}

	var server *webtransport.Server

	mux := http.NewServeMux()
	mux.HandleFunc("/echo", func(w http.ResponseWriter, r *http.Request) {
		session, err := server.Upgrade(w, r)
		if err != nil {
			w.WriteHeader(http.StatusInternalServerError)
			return
		}
		go handleSession(session, r)
	})

	server = &webtransport.Server{
		H3: &http3.Server{
			Addr:      *addr,
			TLSConfig: &tls.Config{Certificates: []tls.Certificate{cert}, NextProtos: []string{http3.NextProtoH3}},
			Handler:   mux,
		},
		// Loopback interop test: accept any Origin so both browser and CLI clients
		// can connect. The origin is reflected back for the client to inspect.
		CheckOrigin: func(r *http.Request) bool { return true },
	}

	conn, err := net.ListenPacket("udp", *addr)
	if err != nil {
		fmt.Fprintln(os.Stderr, "bind error:", err)
		os.Exit(1)
	}
	defer conn.Close()
	fmt.Println("CERTHASH", hash)
	fmt.Println("LISTEN", *addr)
	fmt.Println("READY")
	os.Stdout.Sync()

	if err := server.Serve(conn); err != nil {
		fmt.Fprintln(os.Stderr, "serve error:", err)
		os.Exit(1)
	}
}

func handleSession(session *webtransport.Session, r *http.Request) {
	ctx := session.Context()

	// INFO stream: reflect path/query/origin so the client can verify them reliably.
	info, _ := json.Marshal(map[string]any{
		"path":          r.URL.Path,
		"query":         r.URL.RawQuery,
		"origin":        r.Header.Get("Origin"),
		"cookiePresent": r.Header.Get("Cookie") != "",
	})
	if s, err := session.OpenUniStream(); err == nil {
		s.Write(append([]byte("INFO:"), info...))
		s.Close()
	}

	go echoDatagrams(ctx, session, r.URL.RawQuery)
	go acceptBidi(ctx, session)
	go acceptUni(ctx, session)

	<-ctx.Done()
}

func echoDatagrams(ctx context.Context, session *webtransport.Session, query string) {
	for {
		msg, err := session.ReceiveDatagram(ctx)
		if err != nil {
			return
		}
		// Independent receipt evidence, before sending the echo through the proxy.
		receipt, _ := json.Marshal(struct {
			Query string `json:"query"`
			Data  []byte `json:"data"`
		}{query, msg})
		fmt.Println("RECEIPT", string(receipt))
		_ = session.SendDatagram(msg) // echo exact bytes
	}
}

func acceptBidi(ctx context.Context, session *webtransport.Session) {
	for {
		stream, err := session.AcceptStream(ctx)
		if err != nil {
			return
		}
		go func() {
			io.Copy(stream, stream) // echo, preserve order; returns on client FIN
			stream.Close()          // send our FIN
		}()
	}
}

func acceptUni(ctx context.Context, session *webtransport.Session) {
	for {
		rs, err := session.AcceptUniStream(ctx)
		if err != nil {
			return
		}
		go func() {
			data, _ := io.ReadAll(rs)
			if ss, err := session.OpenUniStream(); err == nil {
				ss.Write(append([]byte("ECHO:"), data...))
				ss.Close()
			}
		}()
	}
}
